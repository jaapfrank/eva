package main

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"github.com/coder/websocket"
	"github.com/purpshell/meowcaller"
	"io"
	"math"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

// Only the actual SDK call exposes these hooks; tests substitute the same PCM boundary.
type audioCall interface {
	ID() string
	Answer() error
	Reject() error
	Hangup() error
	Receive(meowcaller.AudioSink)
	Play(meowcaller.AudioSource) *meowcaller.Player
	OnEnd(func(string))
}
type frameSink struct {
	frames  chan []byte
	cancel  context.CancelFunc
	current func() error
}

func (s *frameSink) WriteFrame(frame []float32) error {
	if len(frame) != meowcaller.FrameSamples {
		return errors.New("invalid_audio_frame")
	}
	if e := s.current(); e != nil {
		s.cancel()
		return e
	}
	data := make([]byte, len(frame)*2)
	for i, v := range frame {
		if math.IsNaN(float64(v)) || math.IsInf(float64(v), 0) {
			s.cancel()
			return errors.New("invalid_audio_sample")
		}
		value := math.Max(-32768, math.Min(32767, float64(v)*32768))
		binary.LittleEndian.PutUint16(data[2*i:], uint16(int16(value)))
	}
	select {
	case s.frames <- data:
		return nil
	default:
		s.cancel()
		return errors.New("audio_backpressure")
	}
}
func (*frameSink) Close() error { return nil }

type readyMessage struct {
	Type       string `json:"type"`
	SampleRate int    `json:"sample_rate"`
	Channels   int    `json:"channels"`
	Encoding   string `json:"encoding"`
}

func connectAudio(ctx context.Context, url, contact, id string, token func() (string, error), call audioCall, current func() error, onReady func()) error {
	if e := current(); e != nil {
		return e
	}
	secret, e := token()
	if e != nil {
		return e
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	headers := http.Header{"Authorization": []string{"Bearer " + secret}, "X-Eva-Contact": []string{contact}, "X-Eva-Call-ID": []string{id}}
	dialCtx, stopDial := context.WithTimeout(ctx, 15*time.Second)
	conn, res, e := websocket.Dial(dialCtx, url, &websocket.DialOptions{HTTPHeader: headers, HTTPClient: &http.Client{Transport: &http.Transport{Proxy: nil}, CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("redirect_refused") }}})
	stopDial()
	if res != nil && e != nil {
		res.Body.Close()
	}
	if e != nil {
		return errors.New("voice_unavailable")
	}
	defer conn.CloseNow()
	conn.SetReadLimit(19200)
	readyCtx, stopReady := context.WithTimeout(ctx, 15*time.Second)
	kind, data, e := conn.Read(readyCtx)
	stopReady()
	if e != nil || kind != websocket.MessageText {
		return errors.New("voice_not_ready")
	}
	var ready readyMessage
	if json.Unmarshal(data, &ready) != nil || ready != (readyMessage{"ready", 16000, 1, "pcm_s16le"}) {
		return errors.New("invalid_voice_format")
	}
	if e = current(); e != nil {
		return e
	}
	reader, writer := io.Pipe()
	defer reader.Close()
	defer writer.Close()
	sink := &frameSink{make(chan []byte, 8), cancel, current}
	call.Receive(sink)
	call.Play(meowcaller.PCMStream(reader))
	call.OnEnd(func(string) { cancel() })
	if e = call.Answer(); e != nil {
		return errors.New("call_not_answered")
	}
	onReady()
	defer call.Hangup()
	var once sync.Once
	finish := func() { once.Do(func() { cancel(); writer.Close(); reader.Close(); conn.CloseNow() }) }
	defer finish()
	done := make(chan error, 2)
	playback := make(chan []byte, 16)
	// WhatsApp must send initial RTP before the relay will forward peer audio.
	// A blocking PCMStream waiting for Eva's first response would deadlock that
	// handshake. Feed silence at native frame cadence whenever no output exists.
	go func() {
		ticker := time.NewTicker(60 * time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
			}
			frame := make([]byte, 1920)
			select {
			case data := <-playback:
				frame = data
			default:
			}
			if _, err := writer.Write(frame); err != nil {
				return
			}
		}
	}()
	go func() {
		pending := make([]byte, 0, 21120)
		for {
			kind, data, e := conn.Read(ctx)
			if e != nil {
				done <- e
				return
			}
			if e = current(); e != nil {
				done <- e
				return
			}
			if kind != websocket.MessageBinary || len(data) == 0 || len(data)%2 != 0 || len(data) > 19200 {
				done <- errors.New("invalid_voice_frame")
				return
			}
			pending = append(pending, data...)
			for len(pending) >= 1920 {
				frame := append([]byte(nil), pending[:1920]...)
				pending = pending[1920:]
				select {
				case playback <- frame:
				default:
					done <- errors.New("speaker_backpressure")
					return
				}
			}
		}
	}()
	go func() {
		for {
			select {
			case <-ctx.Done():
				done <- ctx.Err()
				return
			case frame := <-sink.frames:
				if e := current(); e != nil {
					done <- e
					return
				}
				sendCtx, stop := context.WithTimeout(ctx, 2*time.Second)
				e := conn.Write(sendCtx, websocket.MessageBinary, frame)
				stop()
				if e != nil {
					done <- e
					return
				}
			}
		}
	}()
	guard := time.NewTicker(time.Second)
	defer guard.Stop()
	for {
		select {
		case e := <-done:
			finish()
			return e
		case <-ctx.Done():
			finish()
			return ctx.Err()
		case <-guard.C:
			if e := current(); e != nil {
				finish()
				return e
			}
		}
	}
}
func callMediaURL(value string) bool {
	u, e := url.Parse(value)
	return e == nil && u.Host != "" && (u.Scheme == "ws" || u.Scheme == "wss") && u.User == nil && u.RawQuery == "" && u.Fragment == "" && !strings.ContainsAny(value, "\r\n")
}
func (b *bridge) probeMediaReady(ctx context.Context) {
	ready := false
	defer func() { b.mu.Lock(); b.mediaReady = ready; b.mu.Unlock() }()
	secret, e := b.core.token()
	if e != nil {
		return
	}
	u, e := url.Parse(b.mediaURL)
	if e != nil {
		return
	}
	if u.Scheme == "ws" {
		u.Scheme = "http"
	} else {
		u.Scheme = "https"
	}
	u.Path = "/ready"
	u.RawQuery = ""
	req, e := http.NewRequestWithContext(ctx, "GET", u.String(), nil)
	if e != nil {
		return
	}
	req.Header.Set("Authorization", "Bearer "+secret)
	client := http.Client{Transport: &http.Transport{Proxy: nil}, Timeout: 3 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("redirect_refused") }}
	res, e := client.Do(req)
	if e != nil {
		return
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		return
	}
	data, e := io.ReadAll(io.LimitReader(res.Body, 1025))
	if e != nil || len(data) > 1024 {
		return
	}
	var value struct {
		Ready     bool   `json:"ready"`
		Transport string `json:"transport"`
	}
	if json.Unmarshal(data, &value) != nil {
		return
	}
	ready = value.Ready && value.Transport == "existing_eva_voice"
}
