package main

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"github.com/coder/websocket"
	whatsmeow "github.com/polymorfa/hypermeow"
	"github.com/polymorfa/hypermeow/proto/waE2E"
	"github.com/polymorfa/hypermeow/types"
	"github.com/polymorfa/hypermeow/types/events"
	"github.com/purpshell/meowcaller"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func testJournal(t *testing.T) *journal {
	t.Helper()
	directory := t.TempDir()
	if e := os.Chmod(directory, 0700); e != nil {
		t.Fatal(e)
	}
	j, e := openJournal(directory)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { j.db.Close() })
	return j
}
func TestDurableDedupAndUncertainRestart(t *testing.T) {
	directory := t.TempDir()
	_ = os.Chmod(directory, 0700)
	j, e := openJournal(directory)
	if e != nil {
		t.Fatal(e)
	}
	source := source{JID: "31600000000@s.whatsapp.net", ID: "ORIGINAL", Text: "synthetic", Contact: "31600000000", Account: "31600000001"}
	id := digest(source.JID, source.ID)
	for i := 0; i < 2; i++ {
		ok, e := j.admit(id, source)
		if e != nil || ok != (i == 0) {
			t.Fatalf("admission %v %v", ok, e)
		}
	}
	ok, e := j.claim("reply:"+id, id, "PROVIDER")
	if !ok || e != nil {
		t.Fatal("claim", e)
	}
	if !j.own("PROVIDER") {
		t.Fatal("own message missing")
	}
	j.db.Close()
	j, e = openJournal(directory)
	if e != nil {
		t.Fatal(e)
	}
	defer j.db.Close()
	r, e := j.outgoing("reply:" + id)
	if e != nil || r.State != "ambiguous" {
		t.Fatal("replayed uncertainty", r, e)
	}
	saved, e := j.source(id)
	if e != nil || saved != source {
		t.Fatal("source binding differs", e)
	}
	ok, e = j.claim("reply:"+id, id, "DIFFERENT")
	if ok || e != nil {
		t.Fatal("double dispatch", ok, e)
	}
}
func TestControlAuthConfigureAndRequiredContact(t *testing.T) {
	j := testJournal(t)
	token := "x" + strings.Repeat("t", 40)
	core, _ := newCore("http://127.0.0.1:1", func() (string, error) { return token, nil })
	b, _ := newBridge(t.TempDir(), "ws://127.0.0.1:1/call-audio", j, core)
	server := httptest.NewServer(controlHandler(b, core.token))
	defer server.Close()
	request := func(path, body string, authorized bool) *http.Response {
		method := "POST"
		if path == "/status" {
			method = "GET"
		}
		req, _ := http.NewRequest(method, server.URL+path, strings.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		if authorized {
			req.Header.Set("Authorization", "Bearer "+token)
		}
		res, e := http.DefaultClient.Do(req)
		if e != nil {
			t.Fatal(e)
		}
		return res
	}
	for _, path := range []string{"/status", "/enable", "/disable", "/logout", "/configure"} {
		res := request(path, "{}", false)
		res.Body.Close()
		if res.StatusCode != 401 {
			t.Fatal(path, res.StatusCode)
		}
	}
	res := request("/enable", "{}", true)
	res.Body.Close()
	if res.StatusCode != 422 {
		t.Fatal("contactless enable", res.StatusCode)
	}
	res = request("/configure", `{"contact_phone":"31600000000","audio_enabled":true}`, true)
	var status map[string]any
	_ = json.NewDecoder(res.Body).Decode(&status)
	res.Body.Close()
	if res.StatusCode != 200 || status["enabled"] != false || status["account_phone"] != nil {
		t.Fatal("configuration", status)
	}
	features := status["features"].(map[string]any)
	if features["voice_messages"] != false || features["live_calls"] != true || features["text"] != true {
		t.Fatal("false feature claim", features)
	}
	res = request("/configure", `{"contact_phone":"31600000000","audio_enabled":true,"owner":"other"}`, true)
	res.Body.Close()
	if res.StatusCode != 422 {
		t.Fatal("client scope accepted")
	}
	if b.wa != nil {
		t.Fatal("configure started network")
	}
}
func TestTokenPrivateFileAndCurrentAuthority(t *testing.T) {
	directory := t.TempDir()
	path := filepath.Join(directory, "token")
	if e := os.WriteFile(path, []byte(strings.Repeat("t", 40)), 0600); e != nil {
		t.Fatal(e)
	}
	if _, e := readToken(path); e != nil {
		t.Fatal(e)
	}
	if e := os.Symlink(path, path+"-link"); e != nil {
		t.Fatal(e)
	}
	if _, e := readToken(path + "-link"); e == nil {
		t.Fatal("symlink token accepted")
	}
	_ = os.Chmod(path, 0644)
	if _, e := readToken(path); e == nil {
		t.Fatal("public token accepted")
	}
	j := testJournal(t)
	core, _ := newCore("http://127.0.0.1:1", func() (string, error) { return "synthetic", nil })
	b, _ := newBridge(t.TempDir(), "ws://127.0.0.1:1", j, core)
	b.cfg = settings{true, "31600000000", true, 0}
	b.account = "31600000001"
	b.connection = "connected"
	b.generation = 2
	if e := b.current(2, b.cfg.Contact, b.account, true); e != nil {
		t.Fatal(e)
	}
	if b.current(1, b.cfg.Contact, b.account, true) == nil || b.current(2, "31600000002", b.account, true) == nil {
		t.Fatal("stale authority accepted")
	}
	b.cfg.Audio = false
	if b.current(2, b.cfg.Contact, b.account, true) == nil {
		t.Fatal("disabled speech admitted")
	}
	b.cfg.Audio = true
	b.account = b.cfg.Contact
	if b.current(2, b.cfg.Contact, b.account, true) == nil {
		t.Fatal("self account admitted")
	}
}

type fakeCall struct {
	mu       sync.Mutex
	sink     meowcaller.AudioSink
	source   meowcaller.AudioSource
	end      func(string)
	answered atomic.Int32
	hung     atomic.Int32
	onAnswer func()
}

func (*fakeCall) ID() string { return "SYNTHETIC-CALL" }
func (c *fakeCall) Answer() error {
	c.answered.Add(1)
	if c.onAnswer != nil {
		c.onAnswer()
	}
	return nil
}
func (*fakeCall) Reject() error                    { return nil }
func (c *fakeCall) Hangup() error                  { c.hung.Add(1); return nil }
func (c *fakeCall) Receive(s meowcaller.AudioSink) { c.mu.Lock(); c.sink = s; c.mu.Unlock() }
func (c *fakeCall) Play(s meowcaller.AudioSource) *meowcaller.Player {
	c.mu.Lock()
	c.source = s
	c.mu.Unlock()
	return nil
}
func (c *fakeCall) OnEnd(f func(string)) { c.mu.Lock(); c.end = f; c.mu.Unlock() }
func TestDuplexPCMReadyBeforeAnswerAndExactHeaders(t *testing.T) {
	call := &fakeCall{}
	sent := make(chan []byte, 1)
	readSource := make(chan []float32, 1)
	serverErr := make(chan error, 1)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer synthetic" || r.Header.Get("X-Eva-Contact") != "31600000000" || r.Header.Get("X-Eva-Call-ID") != call.ID() {
			serverErr <- errors.New("headers differ")
			return
		}
		conn, e := websocket.Accept(w, r, nil)
		if e != nil {
			serverErr <- e
			return
		}
		defer conn.CloseNow()
		ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
		defer cancel()
		if call.answered.Load() != 0 {
			serverErr <- errors.New("answered before ready")
			return
		}
		e = conn.Write(ctx, websocket.MessageText, []byte(`{"type":"ready","sample_rate":16000,"channels":1,"encoding":"pcm_s16le"}`))
		if e != nil {
			serverErr <- e
			return
		}
		audio := make([]byte, 1920)
		for i := 0; i < 960; i++ {
			binary.LittleEndian.PutUint16(audio[2*i:], 8192)
		}
		if e = conn.Write(ctx, websocket.MessageBinary, audio); e != nil {
			serverErr <- e
			return
		}
		kind, data, e := conn.Read(ctx)
		if e != nil || kind != websocket.MessageBinary {
			serverErr <- errors.New("no mic PCM")
			return
		}
		sent <- data
		select {
		case <-readSource:
		case <-ctx.Done():
			serverErr <- ctx.Err()
			return
		}
		serverErr <- nil
	}))
	defer server.Close()
	call.onAnswer = func() {
		call.mu.Lock()
		sink, source := call.sink, call.source
		call.mu.Unlock()
		go func() {
			frame := make([]float32, 960)
			for i := range frame {
				frame[i] = 0.5
			}
			_ = sink.WriteFrame(frame)
			frame, e := source.ReadFrame()
			if e == nil {
				if frame[0] != 0.25 {
					t.Error("speaker sample differs", frame[0])
				}
				readSource <- frame
			}
		}()
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	e := connectAudio(ctx, "ws"+strings.TrimPrefix(server.URL, "http"), "31600000000", call.ID(), func() (string, error) { return "synthetic", nil }, call, func() error { return nil }, func() {})
	if e == nil {
		t.Fatal("remote close should end transport")
	}
	if call.answered.Load() != 1 || call.hung.Load() == 0 {
		t.Fatal("call lifecycle differs")
	}
	if e := <-serverErr; e != nil {
		t.Fatal(e)
	}
	data := <-sent
	if len(data) != 1920 || binary.LittleEndian.Uint16(data[:2]) != 16384 {
		t.Fatal("mic PCM differs")
	}
}
func TestReadyRevocationAndBadFormatNeverAnswer(t *testing.T) {
	for _, ready := range []string{`{"type":"ready","sample_rate":48000,"channels":1,"encoding":"pcm_s16le"}`, `{"type":"ready","sample_rate":16000,"channels":1,"encoding":"pcm_s16le"}`} {
		call := &fakeCall{}
		var revoked atomic.Bool
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			conn, e := websocket.Accept(w, r, nil)
			if e != nil {
				return
			}
			defer conn.CloseNow()
			revoked.Store(true)
			_ = conn.Write(r.Context(), websocket.MessageText, []byte(ready))
			_, _, _ = conn.Read(r.Context())
		}))
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		e := connectAudio(ctx, "ws"+strings.TrimPrefix(server.URL, "http"), "31600000000", call.ID(), func() (string, error) { return "synthetic", nil }, call, func() error {
			if revoked.Load() {
				return errors.New("revoked")
			}
			return nil
		}, func() {})
		cancel()
		server.Close()
		if e == nil || call.answered.Load() != 0 {
			t.Fatal("answered despite revocation/bad format")
		}
	}
}

func TestInitialSilenceLetsRelayStartBeforeEvaHasAnySpeech(t *testing.T) {
	call := &fakeCall{}
	observed := make(chan error, 1)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			observed <- err
			return
		}
		defer conn.CloseNow()
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()
		_ = conn.Write(ctx, websocket.MessageText, []byte(`{"type":"ready","sample_rate":16000,"channels":1,"encoding":"pcm_s16le"}`))
		kind, data, err := conn.Read(ctx)
		if err != nil || kind != websocket.MessageBinary || len(data) != 1920 {
			observed <- errors.New("no initial relay frame")
			return
		}
		observed <- nil
	}))
	defer server.Close()
	call.onAnswer = func() {
		go func() {
			call.mu.Lock()
			source, sink := call.source, call.sink
			call.mu.Unlock()
			frame, err := source.ReadFrame()
			if err == nil {
				if frame[0] != 0 {
					t.Error("first relay frame must be silence")
				}
				_ = sink.WriteFrame(frame)
			}
		}()
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	_ = connectAudio(ctx, "ws"+strings.TrimPrefix(server.URL, "http"), "31600000000", call.ID(), func() (string, error) { return "synthetic", nil }, call, func() error { return nil }, func() {})
	if err := <-observed; err != nil {
		t.Fatal(err)
	}
}
func TestAudioBoundsAndNativePCMStream(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	sink := &frameSink{make(chan []byte, 1), cancel, func() error { return nil }}
	if sink.WriteFrame(make([]float32, 959)) == nil {
		t.Fatal("invalid frame accepted")
	}
	if e := sink.WriteFrame(make([]float32, 960)); e != nil {
		t.Fatal(e)
	}
	if sink.WriteFrame(make([]float32, 960)) == nil || ctx.Err() == nil {
		t.Fatal("unbounded queue")
	}
	data := make([]byte, 1920)
	binary.LittleEndian.PutUint16(data, 32767)
	src := meowcaller.PCMStream(io.NopCloser(bytes.NewReader(data)))
	defer src.Close()
	frame, e := src.ReadFrame()
	if e != nil || len(frame) != 960 || frame[0] < 0.99 {
		t.Fatal("real SDK PCM mismatch", e)
	}
}

type fakeSender struct {
	sends   int
	fail    bool
	target  types.JID
	message *waE2E.Message
}

func (*fakeSender) GenerateMessageID() types.MessageID { return "BOT-SYNTHETIC" }
func (s *fakeSender) SendMessage(_ context.Context, target types.JID, message *waE2E.Message, extra ...whatsmeow.SendRequestExtra) (whatsmeow.SendResponse, error) {
	s.sends++
	s.target = target
	s.message = message
	if s.fail {
		return whatsmeow.SendResponse{}, errors.New("uncertain")
	}
	return whatsmeow.SendResponse{ID: extra[0].ID}, nil
}
func TestExactQuoteFreshAuthorizationAndNoUncertainReplay(t *testing.T) {
	j := testJournal(t)
	core, _ := newCore("http://127.0.0.1:1", func() (string, error) { return "synthetic", nil })
	b, _ := newBridge(t.TempDir(), "ws://127.0.0.1:1", j, core)
	b.cfg = settings{true, "31600000000", false, 0}
	b.account = "31600000001"
	b.connection = "connected"
	b.generation = 1
	src := source{JID: "12345@lid", ID: "ORIGINAL", Text: "synthetic question", Contact: b.cfg.Contact, Account: b.account}
	id := digest(src.JID, src.ID)
	_, _ = j.admit(id, src)
	sender := &fakeSender{fail: true}
	auth := func() error { return nil }
	e := b.sendText(context.Background(), 1, sender, "reply:"+id, id, src, "answer", auth)
	if e == nil || sender.sends != 1 {
		t.Fatal("uncertainty lost")
	}
	e = b.sendText(context.Background(), 1, sender, "reply:"+id, id, src, "answer", auth)
	if e == nil || sender.sends != 1 {
		t.Fatal("blind replay")
	}
	if sender.target.String() != src.JID || sender.message.GetExtendedTextMessage().GetContextInfo().GetStanzaID() != src.ID || sender.message.GetExtendedTextMessage().GetContextInfo().GetQuotedMessage().GetConversation() != src.Text {
		t.Fatal("quote/source differs")
	}
	src.ID = "REVOKED"
	id = digest(src.JID, src.ID)
	auth = func() error { b.cfg.Enabled = false; return nil }
	if b.sendText(context.Background(), 1, sender, "reply:"+id, id, src, "answer", auth) == nil || sender.sends != 1 {
		t.Fatal("late revoke dispatched")
	}
}
func TestReadinessProbesAuthenticatedHelperAndDoesNotInferFromHealth(t *testing.T) {
	var status atomic.Int32
	status.Store(200)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/ready" || r.Header.Get("Authorization") != "Bearer synthetic" {
			t.Error("wrong readiness contract")
		}
		w.WriteHeader(int(status.Load()))
		_, _ = w.Write([]byte(`{"ready":true,"transport":"existing_eva_voice"}`))
	}))
	defer server.Close()
	j := testJournal(t)
	core, _ := newCore("http://127.0.0.1:1", func() (string, error) { return "synthetic", nil })
	b, _ := newBridge(t.TempDir(), "ws"+strings.TrimPrefix(server.URL, "http")+"/call-audio", j, core)
	b.probeMediaReady(context.Background())
	if b.status()["media_ready"] != true {
		t.Fatal("actual ready missed")
	}
	status.Store(409)
	b.probeMediaReady(context.Background())
	if b.status()["media_ready"] != false {
		t.Fatal("busy readiness retained")
	}
}

func TestExactSDKOfferAlternatePhoneOnlyForSameCallAndPeer(t *testing.T) {
	peer := types.NewJID("123456789", types.HiddenUserServer)
	owner := types.NewJID("31600000000", types.DefaultUserServer)
	for _, tc := range []struct {
		name, id        string
		peer, alternate types.JID
		good            bool
	}{
		{"exact", "CALL", peer, owner, true},
		{"different call", "OTHER", peer, owner, false},
		{"different peer", "CALL", types.NewJID("987654321", types.HiddenUserServer), owner, false},
		{"missing alternate", "CALL", peer, types.EmptyJID, false},
		{"LID is not phone", "CALL", peer, types.NewJID("31600000000", types.HiddenUserServer), false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			proof := &callIdentities{}
			proof.capture(&events.CallOffer{BasicCallMeta: types.BasicCallMeta{CallID: "CALL", CallCreator: peer, CallCreatorAlt: tc.alternate}})
			phone, ok := proof.phone(tc.id, tc.peer)
			if ok != tc.good || (ok && phone != owner.User) {
				t.Fatal("offer binding differs", phone, ok)
			}
			if ok {
				if _, again := proof.phone(tc.id, tc.peer); again {
					t.Fatal("offer reused")
				}
			}
		})
	}
}

func TestExpiredQRIsClearedDisabledAndCannotOverrideNewConnection(t *testing.T) {
	j := testJournal(t)
	core, _ := newCore("http://127.0.0.1:1", func() (string, error) { return "synthetic", nil })
	b, _ := newBridge(t.TempDir(), "ws://127.0.0.1:1", j, core)
	b.cfg = settings{true, "31600000000", true, 0}
	b.connection = "awaiting_qr"
	b.qr = "synthetic-private-qr"
	b.generation = 3
	if !b.expireQR(3) {
		t.Fatal("QR not expired")
	}
	status := b.status()
	if status["enabled"] != false || status["qr_data_url"] != nil || status["connection"] != "disconnected" || status["last_error"] != "pairing_expired" {
		t.Fatal("stale QR retained", status)
	}
	cfg, e := j.loadSettings()
	if e != nil || cfg.Enabled {
		t.Fatal("expired enable persisted", e)
	}
	b.generation = 5
	b.connection = "connected"
	b.cfg.Enabled = true
	if b.expireQR(3) || b.expireQR(5) || b.status()["connection"] != "connected" {
		t.Fatal("old QR closed a live connection")
	}
}
