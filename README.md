# GPUBerth

**Book, isolate and share expensive GPUs between AI agents.**

You own a few powerful GPUs and run many AI agents. GPUBerth lets each agent reserve GPUs through a
simple API, work in its own isolated VM for a fixed time, keep its files, and share large models
with the other agents. The machine wakes up when it is needed and powers off when it is not.

> **Status: early.** GPUBerth was extracted from a private deployment on Proxmox VE with two
> RTX 5090 cards, where it was tested on real hardware. This generic, config-driven version has
> passed the simulated scheduler tests, but **it has not yet been installed from scratch**. Expect
> to adapt the install steps.

## What it does

- **Booking API:** an agent asks for hours and GPUs, polls until ready, and gets SSH access.
- **Fair queue:** strictly first come, first served, so a large booking is never starved.
- **Seats:** one agent can take all GPUs, or several agents can share them at the same time.
- **Isolated VMs:** each seat has its own network with internet access only.
- **Temporary logins:** SSH certificates expire with the booking.
- **Clean reset:** the VM rolls back to a snapshot after every booking.
- **Own drive and shared drive:** `/shared/my-work` is private per agent and survives resets.
  `/shared/common` holds models and caches that every agent can reuse.
- **Power saving:** Wake-on-LAN starts the host, and it powers off after the last booking.
- **Owner first:** your own VM keeps priority. Bookings wait for your approval while you use it.
- **Public ports:** each seat has ports so that agents can publish a service for the booking's length.

## How it works

```text
AI agent ── HTTPS ──> Broker (small always-on VM)
   │                    ├─ locked SSH key ──> wake helper ──> Wake-on-LAN
   │                    └─ locked SSH key ──> GPU host (gpuberth-ctl)
   │                                            ├─ Seat VM a (GPUs, snapshot "clean")
   └──── SSH certificate ──────────────────────>├─ Seat VM b (GPU,  snapshot "clean")
                                                └─ Storage VM (NFS, one agent's folder per seat)
```

Details are in [docs/architecture.md](docs/architecture.md).

## Requirements

- Proxmox VE with PCI passthrough for your GPUs
- LVM-thin or other snapshot-capable VM storage
- A firewall in front of the agent networks, such as pfSense or OPNsense
- A small always-on machine for the broker, and another on the same LAN for Wake-on-LAN if used
- NVIDIA GPUs. RTX 50-series cards need the **open** kernel modules

## Get started

Follow [docs/install-proxmox.md](docs/install-proxmox.md). Read [SECURITY.md](SECURITY.md) first.
It explains what the isolation does and does not protect against.

Add an agent on the broker. It prints a one-time link to that agent's access file:

```bash
sudo -u gpuberth /opt/gpuberth/venv/bin/python /opt/gpuberth/broker/gpuberth-admin add my-agent
```

Agents can read the same guide, without a token, at `/agent.md` on your server.

## Booking example

```bash
curl -X POST https://gpu.example.com/api/bookings \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"hours\": 2, \"gpus\": 1, \"purpose\": \"train model\", \"ssh_public_key\": \"$(cat key.pub)\"}"
```

| API | Purpose |
|---|---|
| `POST /api/bookings` | Create a booking |
| `GET /api/bookings/{id}` | Status, and login details when ready |
| `POST /api/bookings/{id}/extend` | Add time, if nobody is waiting |
| `DELETE /api/bookings/{id}` | End early or cancel |
| `GET /api/availability` | Free GPUs and queue length |

## Tests

```bash
python3 tests/test_scheduler.py
```

The tests run against a simulated host and need no GPU.

## License

Apache-2.0. See [LICENSE](LICENSE).
