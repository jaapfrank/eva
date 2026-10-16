package main

import (
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	_ "modernc.org/sqlite"
	"os"
	"path/filepath"
	"time"
)

type settings struct {
	Enabled     bool   `json:"enabled"`
	Contact     string `json:"contact_phone"`
	Audio       bool   `json:"audio_enabled"`
	AcceptAfter int64  `json:"accept_after"`
}
type source struct {
	JID      string `json:"jid"`
	ID       string `json:"id"`
	FromMe   bool   `json:"from_me"`
	Text     string `json:"text"`
	Occurred string `json:"occurred_at"`
	Contact  string `json:"contact"`
	Account  string `json:"account"`
}
type receipt struct {
	State    string
	Provider string
}
type journal struct{ db *sql.DB }

func privateDirectory(path string) error {
	if err := os.MkdirAll(path, 0700); err != nil {
		return err
	}
	st, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if !st.IsDir() || st.Mode()&os.ModeSymlink != 0 || st.Mode().Perm()&0077 != 0 || fileOwner(st) != os.Getuid() {
		return errors.New("private_storage_required")
	}
	return nil
}
func openJournal(directory string) (*journal, error) {
	if err := privateDirectory(directory); err != nil {
		return nil, err
	}
	path := filepath.Join(directory, "transport.sqlite")
	if err := privateFile(path); err != nil {
		return nil, err
	}
	db, err := sql.Open("sqlite", "file:"+path+"?_pragma=busy_timeout(5000)&_pragma=journal_mode(DELETE)&_pragma=synchronous(FULL)")
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	_, err = db.Exec(`CREATE TABLE IF NOT EXISTS configuration(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS incoming(id TEXT PRIMARY KEY,source TEXT NOT NULL,state TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS outgoing(id TEXT PRIMARY KEY,source_id TEXT NOT NULL,provider TEXT NOT NULL UNIQUE,state TEXT NOT NULL);
 UPDATE incoming SET state='ambiguous' WHERE state='processing'; UPDATE outgoing SET state='ambiguous' WHERE state='dispatching';`)
	if err != nil {
		db.Close()
		return nil, err
	}
	if err = os.Chmod(path, 0600); err != nil {
		db.Close()
		return nil, err
	}
	return &journal{db}, nil
}
func (j *journal) loadSettings() (settings, error) {
	var s string
	err := j.db.QueryRow("SELECT value FROM configuration WHERE id=1").Scan(&s)
	if errors.Is(err, sql.ErrNoRows) {
		return settings{}, nil
	}
	var v settings
	if err == nil {
		err = json.Unmarshal([]byte(s), &v)
	}
	return v, err
}
func (j *journal) save(v settings) error {
	b, e := json.Marshal(v)
	if e != nil {
		return e
	}
	_, e = j.db.Exec("INSERT INTO configuration VALUES(1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value", string(b))
	return e
}
func digest(jid, id string) string {
	h := sha256.Sum256([]byte(jid + "\x00" + id))
	return hex.EncodeToString(h[:])
}
func (j *journal) admit(id string, s source) (bool, error) {
	b, e := json.Marshal(s)
	if e != nil {
		return false, e
	}
	r, e := j.db.Exec("INSERT OR IGNORE INTO incoming VALUES(?,?,'processing')", id, string(b))
	if e != nil {
		return false, e
	}
	n, e := r.RowsAffected()
	return n == 1, e
}
func (j *journal) source(id string) (source, error) {
	var text string
	var s source
	e := j.db.QueryRow("SELECT source FROM incoming WHERE id=?", id).Scan(&text)
	if e == nil {
		e = json.Unmarshal([]byte(text), &s)
	}
	return s, e
}
func (j *journal) finish(id, state string) error {
	_, e := j.db.Exec("UPDATE incoming SET state=? WHERE id=?", state, id)
	return e
}
func (j *journal) claim(id, src, provider string) (bool, error) {
	r, e := j.db.Exec("INSERT OR IGNORE INTO outgoing VALUES(?,?,?,'dispatching')", id, src, provider)
	if e != nil {
		return false, e
	}
	n, e := r.RowsAffected()
	return n == 1, e
}
func (j *journal) outgoing(id string) (receipt, error) {
	var r receipt
	e := j.db.QueryRow("SELECT state,provider FROM outgoing WHERE id=?", id).Scan(&r.State, &r.Provider)
	return r, e
}
func (j *journal) mark(id, state string) error {
	_, e := j.db.Exec("UPDATE outgoing SET state=? WHERE id=?", state, id)
	return e
}
func (j *journal) own(id string) bool {
	var n int
	return j.db.QueryRow("SELECT COUNT(*) FROM outgoing WHERE provider=?", id).Scan(&n) == nil && n > 0
}
func utc(t time.Time) string { return t.UTC().Format("2006-01-02T15:04:05Z") }
