# Security

GPUBerth is built for **agents you own or trust**. It is not a hardened service for selling GPU
time to strangers.

## What the design protects

| Boundary | How |
|---|---|
| Agent to your internal network | Firewall blocks private ranges and your own public IPs |
| Agent to agent | Separate VM and network per seat |
| Agent to another agent's files | The storage VM exports only that agent's folder to its seat |
| Agent to the host | Normal user, no sudo, no guest agent, no shared folders |
| After a booking | Snapshot rollback; the certificate expires |
| Broker to hosts | Locked SSH keys with fixed commands, limited by source IP |

## Known limits

- **PCIe isolation depends on your board.** GPUs behind a chipset may share a path with your boot
  disk and network card. `pcie_acs_override` only makes separate IOMMU groups appear. It adds no
  isolation. Root in a VM could then attack those devices through the GPU. So **never give agents
  root**, and check real ACS support on your own hardware.
- **A kernel exploit in the VM gives root there.** A snapshot resets the VM, but this booking's
  exported folders are reachable until it ends.
- **`/shared/common` is shared by design.** Any agent can change a file that a later agent runs.
  Treat it as untrusted, and wipe it if you suspect a problem.
- **Tokens decide identity.** Whoever has an agent's token is that agent and sees its folder.
- **Agents have internet access**, so nothing stops an agent from sending its own data out.
- **There is no file access log**, so you cannot later prove what an agent read.
- **Data is not encrypted at rest.** Host root can read it.
- **Public ports have no login or TLS in front of them.** Services run under your public IP.
- **Storage has no redundancy of its own.** Back up what matters.

## Before you deploy

1. From inside each seat, as the agent user, try to reach every internal system. All must fail.
2. As root in a seat, try to mount `/srv/agents/<other-agent>`. It must fail.
3. Confirm that the host has no address on the seat bridges, including IPv6 link-local.
4. Check that seat VMs cannot reach each other.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository, not a public issue.
