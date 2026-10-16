package main

import (
	"context"
	"database/sql"
	"errors"
	whatsmeow "github.com/polymorfa/hypermeow"
	"github.com/polymorfa/hypermeow/proto/waE2E"
	"github.com/polymorfa/hypermeow/types"
	"github.com/polymorfa/hypermeow/types/events"
	"google.golang.org/protobuf/proto"
	"strings"
	"time"
	"unicode/utf8"
)

func (b *bridge) incomingMessage(ctx context.Context, generation uint64, wa *whatsmeow.Client, event *events.Message) {
	info := event.Info
	if info.IsFromMe || info.IsGroup || info.IsIncomingBroadcast() || event.SourceWebMsg != nil || event.IsEdit || info.ID == "" || len(info.ID) > 240 || b.journal.own(info.ID) {
		return
	}
	if info.Chat.Server != types.DefaultUserServer && info.Chat.Server != types.HiddenUserServer {
		return
	}
	contact, e := resolvePhone(ctx, wa, info.Sender)
	if e != nil {
		return
	}
	account := wa.Store.GetJID().User
	if e = b.current(generation, contact, account, false); e != nil {
		return
	}
	b.mu.Lock()
	after := b.cfg.AcceptAfter
	b.mu.Unlock()
	if info.Timestamp.Unix() < after || info.Timestamp.After(time.Now().Add(5*time.Minute)) {
		return
	}
	text := event.Message.GetConversation()
	if text == "" {
		text = event.Message.GetExtendedTextMessage().GetText()
	}
	audio := event.Message.GetAudioMessage() != nil
	if !audio && (strings.TrimSpace(text) == "" || utf8.RuneCountInString(text) > 4096) {
		return
	}
	src := source{info.Chat.String(), info.ID, false, text, utc(info.Timestamp), contact, account}
	id := digest(src.JID, src.ID)
	admitted, e := b.journal.admit(id, src)
	if e != nil || !admitted {
		return
	}
	reply := "Spraakberichten worden niet verwerkt door deze livebelverbinding. Stuur tekst of bel het afzonderlijke Eva-nummer."
	if !audio {
		var response struct {
			Text         string `json:"text"`
			Conversation string `json:"conversation_id"`
			Message      string `json:"message_id"`
		}
		e = b.core.call(ctx, "respond", map[string]string{"contact_phone": contact, "message_id": id, "text": text, "occurred_at": src.Occurred}, &response)
		if e == nil && (response.Conversation != "whatsapp:"+contact || response.Message != "whatsapp:"+contact+":"+id || strings.TrimSpace(response.Text) == "" || len(response.Text) > 65536) {
			e = errors.New("response_binding")
		}
		if e != nil {
			reply = "Ik kon dit bericht niet bevestigen. Controleer eerst of er al een opdracht is gestart."
			b.setError("core_response_unconfirmed")
		} else {
			reply = response.Text
		}
	}
	if b.journal.finish(id, "prepared") != nil {
		return
	}
	e = b.sendText(ctx, generation, wa, "reply:"+id, id, src, reply, nil)
	state := "sent"
	if e != nil {
		state = "ambiguous"
		b.setError("reply_delivery_unconfirmed")
	}
	_ = b.journal.finish(id, state)
}

type messageSender interface {
	GenerateMessageID() types.MessageID
	SendMessage(context.Context, types.JID, *waE2E.Message, ...whatsmeow.SendRequestExtra) (whatsmeow.SendResponse, error)
}

func (b *bridge) sendText(ctx context.Context, generation uint64, wa messageSender, operation, id string, src source, text string, authorize func() error) error {
	if e := b.current(generation, src.Contact, src.Account, false); e != nil {
		return e
	}
	if authorize == nil {
		var value struct {
			Authorized bool `json:"authorized"`
		}
		if e := b.core.call(ctx, "authorize", map[string]string{"contact_phone": src.Contact, "message_id": id}, &value); e != nil {
			return e
		}
		if !value.Authorized {
			return errors.New("delivery_not_authorized")
		}
	} else {
		if e := authorize(); e != nil {
			return e
		}
	}
	if e := b.current(generation, src.Contact, src.Account, false); e != nil {
		return e
	}
	provider := wa.GenerateMessageID()
	claimed, e := b.journal.claim(operation, id, provider)
	if e != nil {
		return e
	}
	if !claimed {
		return errors.New("delivery_already_claimed")
	}
	fail := func(e error) error { _ = b.journal.mark(operation, "ambiguous"); return e }
	if e = b.current(generation, src.Contact, src.Account, false); e != nil {
		return fail(e)
	}
	target, e := types.ParseJID(src.JID)
	if e != nil {
		return fail(e)
	}
	message := &waE2E.Message{ExtendedTextMessage: &waE2E.ExtendedTextMessage{Text: proto.String(text), ContextInfo: &waE2E.ContextInfo{StanzaID: proto.String(src.ID), Participant: proto.String(src.JID), QuotedMessage: &waE2E.Message{Conversation: proto.String(src.Text)}}}}
	// The SDK returns the server-acknowledged ID. Destination and quote are the
	// immutable original private source passed to this single SendMessage call.
	response, e := wa.SendMessage(ctx, target, message, whatsmeow.SendRequestExtra{ID: provider, Timeout: 20 * time.Second})
	if e != nil {
		return fail(e)
	}
	if response.ID != provider {
		return fail(errors.New("provider_receipt_unconfirmed"))
	}
	return b.journal.mark(operation, "sent")
}

type returnItem struct {
	WorkID      string `json:"work_id"`
	OperationID string `json:"operation_id"`
	Summary     string `json:"summary"`
	Truncated   bool   `json:"output_truncated"`
	Title       string `json:"title"`
	Origin      string `json:"origin_message_id"`
	OutputHash  string `json:"output_sha256"`
	ResultHash  string `json:"result_sha256"`
}

func (b *bridge) pollReturn(ctx context.Context) {
	b.mu.Lock()
	generation, contact, account, wa, connected := b.generation, b.cfg.Contact, b.account, b.wa, b.connection == "connected"
	b.mu.Unlock()
	if !connected || wa == nil || b.current(generation, contact, account, false) != nil {
		return
	}
	var envelope struct {
		Result *returnItem `json:"result"`
	}
	if b.core.call(ctx, "return/poll", map[string]string{"contact_phone": contact}, &envelope) != nil {
		return
	}
	r := envelope.Result
	if r == nil {
		return
	}
	if r.WorkID == "" || len(r.WorkID) > 240 || r.OperationID == "" || len(r.OperationID) > 240 || r.Summary == "" || utf8.RuneCountInString(r.Summary) > 600 || !validDigest.MatchString(r.Origin) || !validDigest.MatchString(r.OutputHash) || !validDigest.MatchString(r.ResultHash) {
		b.setError("return_binding_unavailable")
		return
	}
	operation := "return:" + r.OperationID
	receipt, e := b.journal.outgoing(operation)
	if e != nil && !errors.Is(e, sql.ErrNoRows) {
		return
	}
	if e == nil && receipt.State != "sent" {
		return
	}
	confirmed := e == nil
	src, e := b.journal.source(r.Origin)
	if e != nil || b.current(generation, src.Contact, src.Account, false) != nil {
		return
	}
	payload := map[string]string{"contact_phone": contact, "work_id": r.WorkID, "operation_id": r.OperationID}
	authorize := func() error {
		var auth struct {
			Status    string `json:"status"`
			Operation string `json:"operation_id"`
			Output    string `json:"output_sha256"`
			Result    string `json:"result_sha256"`
		}
		if e := b.core.call(ctx, "return/authorize", payload, &auth); e != nil {
			return e
		}
		if auth.Status != "authorized" || auth.Operation != r.OperationID || auth.Output != r.OutputHash || auth.Result != r.ResultHash {
			return errors.New("return_authority_changed")
		}
		return nil
	}
	if confirmed {
		e = authorize()
	} else {
		summary := r.Title + "\n\n" + r.Summary
		if r.Truncated {
			summary += "…"
		}
		summary += "\n\nVolledig resultaat op verzoek. Opdracht: " + r.WorkID
		e = b.sendText(ctx, generation, wa, operation, r.Origin, src, summary, authorize)
	}
	if e != nil || b.current(generation, src.Contact, src.Account, false) != nil {
		return
	}
	var ack struct {
		Status string `json:"status"`
	}
	if b.core.call(ctx, "return/ack", payload, &ack) != nil || ack.Status != "acknowledged" {
		b.setError("background_return_unconfirmed")
	}
}
