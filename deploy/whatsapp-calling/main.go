package main

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"
)

func controlHandler(b *bridge, token func() (string, error)) http.Handler {
	var operation sync.Mutex
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		reply := func(code int, value any) { w.WriteHeader(code); _ = json.NewEncoder(w).Encode(value) }
		authorized := func() bool {
			secret, e := token()
			return e == nil && subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+secret)) == 1
		}
		if !authorized() {
			reply(401, map[string]string{"error": "authentication_required"})
			return
		}
		if r.URL.RawQuery != "" {
			reply(422, map[string]string{"error": "scope_is_server_owned"})
			return
		}
		if r.Method == "GET" && r.URL.Path == "/status" {
			reply(200, b.status())
			return
		}
		if r.Method != "POST" || !strings.Contains("|/configure|/enable|/disable|/logout|", "|"+r.URL.Path+"|") {
			reply(404, map[string]string{"error": "not_found"})
			return
		}
		if strings.TrimSpace(strings.Split(r.Header.Get("Content-Type"), ";")[0]) != "application/json" {
			reply(415, map[string]string{"error": "json_required"})
			return
		}
		data, e := io.ReadAll(io.LimitReader(r.Body, 2049))
		if e != nil || len(data) > 2048 {
			reply(413, map[string]string{"error": "request_too_large"})
			return
		}
		var values map[string]json.RawMessage
		if json.Unmarshal(data, &values) != nil || values == nil {
			reply(422, map[string]string{"error": "invalid_request"})
			return
		}
		var contact string
		var audio bool
		if r.URL.Path == "/configure" {
			if len(values) != 2 || json.Unmarshal(values["contact_phone"], &contact) != nil || json.Unmarshal(values["audio_enabled"], &audio) != nil || strings.TrimSpace(string(values["audio_enabled"])) == "null" || !validPhone.MatchString(contact) {
				reply(422, map[string]string{"error": "invalid_configuration"})
				return
			}
		} else if len(values) != 0 {
			reply(422, map[string]string{"error": "invalid_request"})
			return
		}
		operation.Lock()
		defer operation.Unlock()
		if !authorized() {
			reply(401, map[string]string{"error": "authentication_required"})
			return
		}
		switch r.URL.Path {
		case "/configure":
			e = b.configure(contact, audio)
		case "/enable":
			e = b.enable()
		case "/disable":
			e = b.disable()
		case "/logout":
			e = b.logout()
		}
		if e != nil {
			code := 503
			if e.Error() == "contact_required" || e.Error() == "invalid_configuration" {
				code = 422
			}
			reply(code, map[string]string{"error": "operation_not_confirmed"})
			return
		}
		reply(200, b.status())
	})
}
func env(name, fallback string) string {
	if v := os.Getenv(name); v != "" {
		return v
	}
	return fallback
}
func run() error {
	syscall.Umask(0077)
	token := func() (string, error) {
		return readToken(env("EVA_WHATSAPP_CONTROL_TOKEN_FILE", "/run/secrets/dashboard_access_token"))
	}
	if _, e := token(); e != nil {
		return e
	}
	media := env("EVA_WHATSAPP_CALL_MEDIA_URL", "ws://host.docker.internal:44731/call-audio")
	if !callMediaURL(media) {
		return errors.New("invalid_media_url")
	}
	core, e := newCore(env("EVA_WHATSAPP_CORE_URL", "http://core:8080"), token)
	if e != nil {
		return e
	}
	directory := env("EVA_WHATSAPP_DATA_DIRECTORY", "/var/lib/eva-whatsapp")
	journal, e := openJournal(directory)
	if e != nil {
		return e
	}
	defer journal.db.Close()
	b, e := newBridge(directory, media, journal, core)
	if e != nil {
		return e
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	server := &http.Server{Addr: ":" + env("PORT", "8080"), Handler: controlHandler(b, token), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 15 * time.Second, WriteTimeout: 30 * time.Second, IdleTimeout: 30 * time.Second}
	errors := make(chan error, 1)
	go func() { errors <- server.ListenAndServe() }()
	if b.cfg.Enabled {
		if e = b.enable(); e != nil {
			b.setError("startup_connection_unavailable")
		}
	}
	go func() {
		ticker := time.NewTicker(3 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				b.pollReturn(ctx)
			}
		}
	}()
	go func() {
		b.probeMediaReady(ctx)
		ticker := time.NewTicker(5 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				b.probeMediaReady(ctx)
			}
		}
	}()
	select {
	case <-ctx.Done():
	case e = <-errors:
		if e != http.ErrServerClosed {
			return e
		}
	}
	b.mu.Lock()
	b.stopLocked()
	wa, auth := b.wa, b.auth
	b.wa = nil
	b.mu.Unlock()
	if wa != nil {
		wa.Disconnect()
	}
	if auth != nil {
		auth.Close()
	}
	end, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	return server.Shutdown(end)
}
func main() {
	if len(os.Args) == 2 && os.Args[1] == "--healthcheck" {
		secret, err := readToken(env("EVA_WHATSAPP_CONTROL_TOKEN_FILE", "/run/secrets/dashboard_access_token"))
		if err != nil {
			os.Exit(1)
		}
		req, err := http.NewRequest("GET", "http://127.0.0.1:"+env("PORT", "8080")+"/status", nil)
		if err != nil {
			os.Exit(1)
		}
		req.Header.Set("Authorization", "Bearer "+secret)
		client := http.Client{Timeout: 2 * time.Second, Transport: &http.Transport{Proxy: nil}}
		response, err := client.Do(req)
		if err != nil {
			os.Exit(1)
		}
		response.Body.Close()
		if response.StatusCode != 200 {
			os.Exit(1)
		}
		return
	}
	if e := run(); e != nil {
		_, _ = os.Stderr.WriteString("WhatsApp transport unavailable.\n")
		os.Exit(1)
	}
}
