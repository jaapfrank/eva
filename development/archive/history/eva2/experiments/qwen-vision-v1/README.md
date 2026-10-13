# Isolated Qwen vision candidate

Copy these files to a separate artifact directory before running prepare.py.
It downloads about4.18GB, verifies pinned published hashes, and extracts the
official CUDA runtime there. Do not run preparation inside the source repository.
The optional model is not installed into Eva and no services are replaced.

probe.py uses the recorded blank.png from the earlier local-vision artifact
folder. It starts a loopback-only temporary llama-server, submits one bounded
image request and stops the server in finally. No camera data or cloud model is
used. Default log verbosity omitted device detail; verify-gpu.py performs only
startup with verbose logging, records GPU process visibility and stops, without
a second generation. Its WSL nvidia-smi process memory values are unavailable.

The original probe script is retained for reproducibility, including deprecated
chat_template_kwargs. Future calls can use --reasoning off without that option;
this does not justify repeating the same completed case. A GPU allocation log
is not a peak-memory or GPU-utilization measurement for the timed generation.

A real camera quality comparison is required before production adoption. No
background camera watcher, process persistence or initiative admission is enabled.
