# Architecture

## Parts

| Part | Runs on | Role |
|---|---|---|
| Broker (`broker/app.py`) | Small always-on VM | API, queue, owner page, SSH certificates, SQLite |
| `gpuberth-ctl` | GPU host | Fixed commands: start, stop, share, power off |
| `gpuberth-wake` | Always-on LAN machine | Sends the Wake-on-LAN packet |
| Seat VMs | GPU host | One agent each, reset after every booking |
| Storage VM | GPU host | NFS for agent folders and common files |

## A booking

1. The agent sends hours, GPUs, purpose and its public SSH key.
2. The broker queues it. Only the head of the queue may start.
3. If the host is off, the broker wakes it. If the owner VM is busy, the booking waits for approval.
4. `gpuberth-ctl` sets the GPUs, RAM and cores for this booking and starts the seat VM.
5. The broker writes the booking ID as the only allowed SSH principal in the VM.
6. The storage VM exports this agent's folder and the common folder to this seat only.
7. The broker mounts them, confirms the GPU count, and regenerates the GPU container config.
8. The broker signs a certificate that expires with the booking, and the status becomes `ready`.
9. At the end, the VM stops and rolls back to `clean`, and the exports are removed.
10. After the last booking, the host returns to how it was found: owner VM on, or powered off.

## Decisions

**Why a certificate plus a per-booking principal?** The certificate expires by itself, even if the
broker fails. The principal stops an unexpired certificate from an earlier booking from working in
a later one.

**Why NFS instead of a disk per agent?** Proxmox ties a disk to one VM, and moving disks between
seats is fragile. Two running VMs cannot share a normal disk either. Per-seat NFS exports with
`all_squash` and `subtree_check` give the same result: root in a VM reaches only what is exported.

**Why strict first come, first served?** If small bookings could pass, a booking for all GPUs might
never start.

**Why a separate network per seat?** VMs on one network reach each other directly, without passing
the firewall.
