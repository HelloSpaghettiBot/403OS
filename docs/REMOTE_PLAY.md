# Remote PC setup and acceptance

Moonlight and Tailscale are selected in the default image. Sunshine runs on
the main PC. Runtime checks are distinct from pairing, streaming or enrolled
identity; those acceptance tests need an authorized PC and tailnet and have
not run here.

## Main PC

Install the official Sunshine build matching the PC's operating system and
follow its platform-specific capture/input setup. Start Sunshine, open its
local web interface at `https://localhost:47990`, create private credentials
and configure the desktop or game application to stream. The initial local
certificate is self-signed; check that this is your own PC before proceeding.
Pairing uses the web interface's PIN page. See the official
[Sunshine getting-started guide](https://docs.lizardbyte.dev/projects/sunshine/latest/md_docs_2getting__started.html).

## Surface LAN test

1. Connect the Surface to the PC's LAN using the WiFi panel.
2. Open Moonlight, discover a host or enter the PC's local hostname/address.
3. Request pairing and enter the displayed PIN on the PC's Sunshine PIN page.
4. List applications and select the configured desktop/game.
5. Begin at 720p/60 with a modest bitrate. Check video, stereo audio, keyboard,
   touch pointer and controller input. Use HOME controls to close the client
   and verify the shell resumes. Repeat after disconnect/reconnect.

The current client uses FFmpeg software decoding. Intel media-driver and
VA-API diagnostics are packaged, but the Moonlight backend has no validated
VA-API path. Higher resolutions, 60 FPS and hardware decoding remain measured
acceptance tasks. Record decode/frame-drop statistics, CPU/RAM use, latency,
power and suspend/resume results on the actual HD 615.

## Tailscale test

Install Tailscale on the main PC. Open the Surface's Tailscale panel, request
login, and open the displayed authentication URL to authorize it. Join both
devices to a tailnet you control or are permitted to use. Select the PC's
Tailscale address or permitted MagicDNS name in Moonlight. Tailscale assigns
stable device addresses and supports machine names through MagicDNS. Follow
the official [Tailscale quickstart](https://tailscale.com/docs/how-to/quickstart)
and your tailnet's access policy.

First prove the LAN stream independently of Tailscale. Then test from another
network: pairing/launch, video/audio/input, interruptions and HOME return.
Cold-boot the Surface twice and verify the enrolled identity survives without
logging in again. Test WiFi reconnection and suspend/resume separately. The
unenrolled empty-state persistence check in a VM does not prove this workflow.

Pairing material and daemon state stay private on DATA. Keep raw state files,
auth URLs and account identifiers out of shared logs. Record sanitized
results and failures in the hardware matrix; do not mark streaming passed
from a help/version command or a daemon process alone.
