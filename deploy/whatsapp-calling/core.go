package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"syscall"
	"time"
)

func fileOwner(info os.FileInfo) int {
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		return -1
	}
	return int(stat.Uid)
}
func privateFile(path string) error {
	st, e := os.Lstat(path)
	if os.IsNotExist(e) {
		return nil
	}
	if e != nil {
		return e
	}
	if !st.Mode().IsRegular() || st.Mode().Perm()&0077 != 0 || fileOwner(st) != os.Getuid() {
		return errors.New("private_file_required")
	}
	return nil
}
func readToken(path string) (string, error) {
	fd, e := syscall.Open(path, syscall.O_RDONLY|syscall.O_NOFOLLOW, 0)
	if e != nil {
		return "", e
	}
	f := os.NewFile(uintptr(fd), path)
	defer f.Close()
	st, e := f.Stat()
	if e != nil {
		return "", e
	}
	if !st.Mode().IsRegular() || st.Size() < 32 || st.Size() > 1025 || st.Mode().Perm()&0007 != 0 {
		return "", errors.New("control_token_unavailable")
	}
	b, e := io.ReadAll(io.LimitReader(f, 1026))
	if e != nil {
		return "", e
	}
	v := strings.TrimSpace(string(b))
	if len(v) < 32 || len(v) > 1024 || strings.ContainsAny(v, " \r\n\t") {
		return "", errors.New("control_token_unavailable")
	}
	return v, nil
}

type coreClient struct {
	base   string
	token  func() (string, error)
	client *http.Client
}

func newCore(base string, token func() (string, error)) (*coreClient, error) {
	u, e := url.Parse(base)
	if e != nil || u == nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") {
		return nil, errors.New("invalid_core_url")
	}
	return &coreClient{strings.TrimRight(base, "/"), token, &http.Client{Transport: &http.Transport{Proxy: nil}, Timeout: 120 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("redirect_refused") }}}, nil
}
func (c *coreClient) call(ctx context.Context, path string, payload any, out any) error {
	b, e := json.Marshal(payload)
	if e != nil {
		return e
	}
	token, e := c.token()
	if e != nil {
		return e
	}
	req, e := http.NewRequestWithContext(ctx, "POST", c.base+"/internal/v1/whatsapp/"+path, bytes.NewReader(b))
	if e != nil {
		return e
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	res, e := c.client.Do(req)
	if e != nil {
		return errors.New("core_unavailable")
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		return errors.New("core_unavailable")
	}
	data, e := io.ReadAll(io.LimitReader(res.Body, 256*1024+1))
	if e != nil || len(data) > 256*1024 {
		return errors.New("core_response_size")
	}
	return json.Unmarshal(data, out)
}
