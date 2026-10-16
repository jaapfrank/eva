package main

import (
	"github.com/polymorfa/hypermeow/types"
	"github.com/polymorfa/hypermeow/types/events"
	"sync"
	"time"
)

// One connection's short-lived SDK offer evidence, not an identity registry.
// Hypermeow dispatches handlers synchronously in registration order. Capture is
// registered before Meowcaller, which then decrypts the same offer and emits Call.
type callIdentity struct {
	peer, alternate types.JID
	received        time.Time
}
type callIdentities struct {
	mu     sync.Mutex
	offers map[string]callIdentity
}

func (p *callIdentities) capture(offer *events.CallOffer) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.offers == nil {
		p.offers = make(map[string]callIdentity)
	}
	now := time.Now()
	for id, evidence := range p.offers {
		if now.Sub(evidence.received) > 2*time.Minute {
			delete(p.offers, id)
		}
	}
	if offer.CallID == "" || len(offer.CallID) > 160 || !offer.GroupJID.IsEmpty() {
		return
	}
	if len(p.offers) >= 16 {
		return
	}
	peer := offer.CallCreator
	if peer.IsEmpty() {
		peer = offer.From
	}
	p.offers[offer.CallID] = callIdentity{peer, offer.CallCreatorAlt, now}
}
func (p *callIdentities) phone(callID string, peer types.JID) (string, bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	evidence, ok := p.offers[callID]
	delete(p.offers, callID)
	if !ok || evidence.peer != peer || time.Since(evidence.received) > 2*time.Minute {
		return "", false
	}
	if peer.Server == types.DefaultUserServer && validPhone.MatchString(peer.User) {
		return peer.User, true
	}
	alt := evidence.alternate.ToNonAD()
	if peer.Server != types.HiddenUserServer || alt.Server != types.DefaultUserServer || !validPhone.MatchString(alt.User) {
		return "", false
	}
	return alt.User, true
}
