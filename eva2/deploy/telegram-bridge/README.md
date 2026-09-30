# Installed Telegram bridge source

This is the versioned source asset for the existing Bottie Telegram adapter,
not a second ingress. Install its two `src/` files at the same paths in the
existing Bottie checkout, apply `telegram-draft-wiring.patch` there, then
restart the existing `eva.service` once. The one-line presentation change preserves
the queue lifecycle while applying the existing Eva2 label.
The pure command/history helper is retained unchanged. Credentials, bot,
conversation routing and server authorization remain in the existing runtime.
Run the focused draft-lane test with Node before installation. Production
source hashes and the immutable parent checkpoint are recorded in the delivery
receipt; owner acceptance remains separate.
