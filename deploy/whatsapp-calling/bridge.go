package main

import (
	"context"
	"encoding/base64"
	"errors"
	whatsmeow "github.com/polymorfa/hypermeow"
	"github.com/polymorfa/hypermeow/store/sqlstore"
	"github.com/polymorfa/hypermeow/types"
	"github.com/polymorfa/hypermeow/types/events"
	waLog "github.com/polymorfa/hypermeow/util/log"
	"github.com/purpshell/meowcaller"
	"github.com/rs/zerolog"
	qrcode "github.com/skip2/go-qrcode"
	"os"
	"path/filepath"
	"regexp"
	"sync"
	"time"
)

var validPhone = regexp.MustCompile(`^[1-9][0-9]{6,14}$`)
var validDigest = regexp.MustCompile(`^[0-9a-f]{64}$`)

type bridge struct {
	mu                                            sync.Mutex
	journal                                       *journal
	cfg                                           settings
	core                                          *coreClient
	directory, mediaURL                           string
	generation                                    uint64
	connection, qr, account, lastError, callState string
	mediaReady                                    bool
	wa                                            *whatsmeow.Client
	calls                                         *meowcaller.Client
	auth                                          *sqlstore.Container
	connectionCancel                              context.CancelFunc
	callCancel                                    context.CancelFunc
	activeCall                                    audioCall
}

func newBridge(directory, mediaURL string, j *journal, core *coreClient) (*bridge, error) {
	cfg, e := j.loadSettings()
	if e != nil {
		return nil, e
	}
	return &bridge{journal: j, cfg: cfg, core: core, directory: directory, mediaURL: mediaURL, connection: "disabled", callState: "idle"}, nil
}
func nullable(s string) any {
	if s == "" {
		return nil
	}
	return s
}
func (b *bridge) status() map[string]any {
	b.mu.Lock()
	defer b.mu.Unlock()
	return map[string]any{"contract": "EvaWhatsApp/v1", "enabled": b.cfg.Enabled, "connection": b.connection, "contact_phone": nullable(b.cfg.Contact), "account_phone": nullable(b.account), "qr_data_url": nullable(b.qr), "audio_enabled": b.cfg.Audio, "last_error": nullable(b.lastError), "features": map[string]bool{"live_calls": true, "voice_messages": false, "text": true}, "media_ready": b.mediaReady, "call_state": b.callState}
}
func (b *bridge) setError(value string) { b.mu.Lock(); b.lastError = value; b.mu.Unlock() }
func (b *bridge) current(generation uint64, contact, account string, voice bool) error {
	if _, e := b.core.token(); e != nil {
		return e
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.generation != generation || !b.cfg.Enabled || b.connection != "connected" || b.cfg.Contact != contact || b.account != account || account == contact || account == "" || (voice && !b.cfg.Audio) {
		return errors.New("channel_authority_changed")
	}
	return nil
}
func (b *bridge) stopLocked() {
	b.generation++
	if b.connectionCancel != nil {
		b.connectionCancel()
		b.connectionCancel = nil
	}
	if b.callCancel != nil {
		b.callCancel()
		b.callCancel = nil
	}
	b.qr = ""
	b.callState = "idle"
}
func (b *bridge) configure(contact string, audio bool) error {
	if !validPhone.MatchString(contact) {
		return errors.New("invalid_configuration")
	}
	b.mu.Lock()
	cfg := settings{false, contact, audio, 0}
	if e := b.journal.save(cfg); e != nil {
		b.mu.Unlock()
		return e
	}
	b.cfg = cfg
	b.stopLocked()
	wa := b.wa
	b.wa = nil
	b.connection = "disabled"
	b.lastError = ""
	b.mu.Unlock()
	if wa != nil {
		wa.Disconnect()
	}
	return nil
}
func (b *bridge) disable() error {
	b.mu.Lock()
	cfg := b.cfg
	cfg.Enabled = false
	if e := b.journal.save(cfg); e != nil {
		b.mu.Unlock()
		return e
	}
	b.cfg = cfg
	b.stopLocked()
	wa := b.wa
	b.wa = nil
	b.connection = "disabled"
	b.mu.Unlock()
	if wa != nil {
		wa.Disconnect()
	}
	return nil
}
func (b *bridge) logout() error {
	b.mu.Lock()
	wa, auth := b.wa, b.auth
	cfg := b.cfg
	cfg.Enabled = false
	if e := b.journal.save(cfg); e != nil {
		b.mu.Unlock()
		return e
	}
	b.cfg = cfg
	b.stopLocked()
	b.wa = nil
	b.connection = "logged_out"
	b.account = ""
	b.mu.Unlock()
	if wa != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		e := wa.Logout(ctx)
		wa.Disconnect()
		if e != nil {
			if wa.Store.ID != nil {
				_ = wa.Store.Delete(context.Background())
			}
			b.setError("logout_unconfirmed")
		}
	}
	if auth != nil {
		devices, e := auth.GetAllDevices(context.Background())
		if e != nil {
			return e
		}
		for _, device := range devices {
			if e = device.Delete(context.Background()); e != nil {
				return e
			}
		}
	}
	return nil
}
func (b *bridge) enable() error {
	b.mu.Lock()
	if !validPhone.MatchString(b.cfg.Contact) {
		b.mu.Unlock()
		return errors.New("contact_required")
	}
	if b.cfg.Enabled && b.wa != nil && (b.connection == "connected" || b.connection == "connecting" || b.connection == "awaiting_qr") {
		b.mu.Unlock()
		return nil
	}
	cfg := b.cfg
	cfg.Enabled = true
	cfg.AcceptAfter = time.Now().Unix()
	if e := b.journal.save(cfg); e != nil {
		b.mu.Unlock()
		return e
	}
	b.cfg = cfg
	b.stopLocked()
	generation := b.generation
	previous := b.wa
	b.wa = nil
	ctx, cancel := context.WithCancel(context.Background())
	b.connectionCancel = cancel
	b.connection = "connecting"
	b.lastError = ""
	b.mu.Unlock()
	if previous != nil {
		previous.Disconnect()
	}
	return b.openConnection(ctx, generation)
}
func (b *bridge) openConnection(ctx context.Context, generation uint64) error {
	path := filepath.Join(b.directory, "session.sqlite")
	if e := privateFile(path); e != nil {
		return e
	}
	auth, e := sqlstore.New(ctx, "sqlite", "file:"+path+"?_pragma=foreign_keys(1)&_pragma=busy_timeout(5000)", waLog.Noop)
	if e != nil {
		return e
	}
	if e = os.Chmod(path, 0600); e != nil {
		auth.Close()
		return e
	}
	device, e := auth.GetFirstDevice(ctx)
	if e != nil {
		auth.Close()
		return e
	}
	wa := whatsmeow.NewClient(device, waLog.Noop)
	identities := &callIdentities{}
	wa.AddEventHandler(func(event any) {
		if offer, ok := event.(*events.CallOffer); ok {
			identities.capture(offer)
		}
	})
	calls := meowcaller.NewClient(wa, meowcaller.WithLogger(zerolog.Nop()))
	b.mu.Lock()
	if b.generation != generation || !b.cfg.Enabled {
		b.mu.Unlock()
		auth.Close()
		return errors.New("connection_canceled")
	}
	oldAuth := b.auth
	b.auth = auth
	b.wa = wa
	b.calls = calls
	b.mu.Unlock()
	if oldAuth != nil {
		oldAuth.Close()
	}
	calls.OnIncomingCall(func(call *meowcaller.Call) { go b.incomingCall(ctx, generation, wa, identities, call) })
	wa.AddEventHandler(func(event any) {
		switch v := event.(type) {
		case *events.Connected:
			account := wa.Store.GetJID().User
			b.mu.Lock()
			if b.generation != generation {
				b.mu.Unlock()
				return
			}
			b.account = account
			b.qr = ""
			if account == b.cfg.Contact {
				b.lastError = "separate_eva_account_required"
				b.connection = "disconnected"
				b.mu.Unlock()
				wa.Disconnect()
				return
			}
			b.connection = "connected"
			b.lastError = ""
			b.mu.Unlock()
			// The pinned SDK requires a push name before it can announce the
			// availability needed for incoming call signaling on a fresh device.
			if wa.Store.PushName == "" {
				wa.Store.PushName = "Eva"
			}
			_ = wa.SendPresence(ctx, types.PresenceAvailable)
		case *events.Disconnected:
			b.mu.Lock()
			if b.generation == generation {
				b.connection = "disconnected"
				b.qr = ""
				if b.callCancel != nil {
					b.callCancel()
				}
			}
			b.mu.Unlock()
		case *events.LoggedOut:
			b.mu.Lock()
			if b.generation == generation {
				b.cfg.Enabled = false
				_ = b.journal.save(b.cfg)
				b.connection = "logged_out"
				b.qr = ""
				b.lastError = "account_logged_out"
				if b.callCancel != nil {
					b.callCancel()
				}
			}
			b.mu.Unlock()
		case *events.Message:
			go b.incomingMessage(ctx, generation, wa, v)
		}
	})
	if wa.Store.ID == nil {
		qr, e := wa.GetQRChannel(ctx)
		if e != nil {
			return e
		}
		go func() {
			completed := false
			defer func() {
				if !completed && b.expireQR(generation) {
					wa.Disconnect()
				}
			}()
			for event := range qr {
				if event.Event == "code" {
					png, e := qrcode.Encode(event.Code, qrcode.Medium, 280)
					if e != nil {
						continue
					}
					b.mu.Lock()
					if b.generation == generation && b.cfg.Enabled {
						b.qr = "data:image/png;base64," + base64.StdEncoding.EncodeToString(png)
						b.connection = "awaiting_qr"
					}
					b.mu.Unlock()
				} else if event.Event == "success" {
					completed = true
					b.mu.Lock()
					if b.generation == generation {
						b.qr = ""
						if b.connection != "connected" {
							b.connection = "connecting"
						}
					}
					b.mu.Unlock()
				} else {
					return
				}
			}
		}()
	}
	if e = wa.Connect(); e != nil {
		b.setError("connection_unavailable")
		return errors.New("connection_unavailable")
	}
	return nil
}
func resolvePhone(ctx context.Context, wa *whatsmeow.Client, jid types.JID) (string, error) {
	jid = jid.ToNonAD()
	if jid.Server == types.HiddenUserServer {
		var e error
		jid, e = wa.Store.LIDs.GetPNForLID(ctx, jid)
		if e != nil {
			return "", e
		}
	}
	if jid.Server != types.DefaultUserServer || !validPhone.MatchString(jid.User) {
		return "", errors.New("caller_phone_unproven")
	}
	return jid.User, nil
}
func (b *bridge) expireQR(generation uint64) bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.generation != generation || b.connection == "connected" {
		return false
	}
	b.cfg.Enabled = false
	_ = b.journal.save(b.cfg)
	b.stopLocked()
	b.connection = "disconnected"
	b.lastError = "pairing_expired"
	return true
}

func (b *bridge) incomingCall(parent context.Context, generation uint64, wa *whatsmeow.Client, identities *callIdentities, call *meowcaller.Call) {
	_, group := call.GroupState()
	if group || call.IsVideo() {
		_ = call.Reject()
		return
	}
	ctx, cancel := context.WithCancel(parent)
	defer cancel()
	contact, proved := identities.phone(call.ID(), call.Peer())
	var e error
	if !proved {
		contact, e = resolvePhone(ctx, wa, call.Peer())
	}
	if e != nil {
		_ = call.Reject()
		return
	}
	account := wa.Store.GetJID().User
	if e = b.current(generation, contact, account, true); e != nil {
		_ = call.Reject()
		return
	}
	b.mu.Lock()
	if b.activeCall != nil {
		b.mu.Unlock()
		_ = call.Reject()
		return
	}
	b.activeCall = call
	b.callCancel = cancel
	b.callState = "connecting"
	b.mu.Unlock()
	defer func() {
		_ = call.Hangup()
		b.mu.Lock()
		if b.activeCall == call {
			b.activeCall = nil
			b.callCancel = nil
			b.callState = "idle"
		}
		b.mu.Unlock()
	}()
	e = connectAudio(ctx, b.mediaURL, contact, call.ID(), b.core.token, call, func() error { return b.current(generation, contact, account, true) }, func() {
		b.mu.Lock()
		if b.generation == generation {
			b.callState = "active"
		}
		b.mu.Unlock()
	})
	if e != nil && ctx.Err() == nil {
		b.setError("live_call_unavailable")
	}
}
