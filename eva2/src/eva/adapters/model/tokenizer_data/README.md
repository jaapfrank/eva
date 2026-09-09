# Local input-envelope tokenizer data

The bundled file is the unmodified OpenAI `o200k_base` BPE vocabulary from
https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken.
SHA-256: `446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d`.
The filename is the URL SHA-1 expected by tiktoken's local cache.

Used by pinned `tiktoken==0.14.0`; its MIT license is included in
`LICENSE.tiktoken`. This is a local envelope estimate for admission, not an
assertion of GPT-6 Astra's private tokenizer. The runtime retains provider-reported
usage as the measured consumption and never downloads tokenizer data at runtime.
