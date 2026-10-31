# GPUBerth access for AI agents

You can book a Linux VM with GPUs for a limited time and work on it by SSH.
Read this whole file before you start.

**This file contains a secret token. Treat it like a password. Do not print it, share it or
commit it to a repository.**

| Item | Value |
|---|---|
| Booking API | {{URL}}/api/bookings |
| Token | `{{TOKEN}}` |
| Agent name | {{AGENT}} |
| SSH address | {{HOST}}, user `agent`; the port comes from your booking answer |
| VM | Ubuntu with NVIDIA GPUs; `/shared` is kept between bookings |
| Public service ports | 4 ports on {{HOST}}, TCP and UDP; listed in your booking answer |
| Booking length | 0.25 to {{MAX_HOURS}} hours |
| Limits | {{DAILY_HOURS}} hours per 24 hours, {{MAX_OPEN}} open bookings |

## Rules

- **Everything outside `/shared` is deleted when the booking ends.**
- Save your work only in `/shared/my-work`. Do not write into other agents' folders.
- Your time starts when the status is `ready`, not when you book.
- Book only the time you need, and end the booking early when you finish, so the machine can
  power off.
- You are the normal user `agent`, without root or sudo. Use Python venv, pip or conda in your
  home folder. GPU containers work with rootless podman:
  `podman run --rm --device nvidia.com/gpu=all docker.io/library/ubuntu:24.04 nvidia-smi -L`
- The VM has internet, but it cannot reach any internal network and cannot send mail on port 25.

## Keeping files between bookings

Everything in the VM is deleted when a booking ends, except these two folders:

| Folder | Who sees it | Use it for |
|---|---|---|
| `/shared/my-work` | Only you; this is your own folder | Your code, data, checkpoints and results |
| `/shared/common` | All agents; any agent can change or delete files here | Large models and download caches |

- **Save your work only in `/shared/my-work`.** Do not put anything private in `/shared/common`.
- Put large models in `/shared/common`, so you and other agents do not download them again.
  Check there first. `HF_HOME`, `TORCH_HOME`, `PIP_CACHE_DIR`, `UV_CACHE_DIR` and
  `CONDA_PKGS_DIRS` already point to it.
- Build venvs in `/shared/my-work` if you want to keep them. Your home folder is deleted.
- All folders share one disk. Delete what you no longer need.
- These folders are network folders: fast for large files, but keep databases and many tiny
  files in your home folder while working, and copy them to `/shared/my-work` at the end.
- **There is no backup.** Copy important results off the VM as well.
- Never store passwords or API keys in either folder.

## Public ports for your services

Four ports are open to the internet, TCP and UDP. They are in `public_ports` in your booking
answer: {{PORTS}}. Start a service in
the VM on one of them, listening on `0.0.0.0`, and it is reachable worldwide at
`{{HOST}}:PORT`. Example: `python3 -m http.server PORT --bind 0.0.0.0`

- There is no login or HTTPS in front of these ports. Protect your service yourself.
- Behind NAT, all clients may appear with one gateway IP, so you may not see real client IPs.
- The ports work only during your booking, and stop when it ends.

## Choosing 1 or 2 GPUs

Add `gpus` to your booking. Without it you get all GPUs. Ask for only what you need.

| You ask for | You get |
|---|---|
| All GPUs | The whole machine |
| Fewer GPUs | A share of CPU and RAM; another agent can work at the same time |

Check what is free with `GET /api/availability`. It answers `free_gpus` and `waiting_bookings`.
Bookings are strictly first come, first served, so a booking for all GPUs waits until they are all free
and later bookings wait behind it.

**Always use `ssh_port`, `ssh` and `public_ports` from the booking answer.** They differ per
booking, because each booking runs on a seat with its own ports.

## What the status means

| status | meaning | what to do |
|---|---|---|
| queued | waiting; `position` shows your place | keep checking |
| waiting_approval | the owner is using the machine and must approve | keep checking, or tell your user |
| starting | machine is being prepared; about 1 to 3 minutes | keep checking |
| ready | VM is ready | save the certificate and connect |
| ending, done | finished | nothing |
| failed | see `error` | book again, or tell your user if it repeats |
| cancelled | cancelled by you or the owner | book again if needed |

## Windows PowerShell

Run this block to book and wait. Change `hours` and `purpose` first. It keeps the key in your
user folder, because Windows SSH refuses private keys with open permissions.

```powershell
$Token = "{{TOKEN}}"
$Api = "{{URL}}/api/bookings"
$Headers = @{ Authorization = "Bearer $Token" }
$Dir = "$HOME\gpu-booking"
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
Set-Location $Dir
if (-not (Test-Path gpukey)) { cmd /c 'ssh-keygen -q -t ed25519 -N "" -f gpukey' }

$Body = @{
    hours = 1
    gpus = 2
    purpose = "describe your job"
    ssh_public_key = (Get-Content gpukey.pub -Raw).Trim()
} | ConvertTo-Json
$Booking = Invoke-RestMethod -Method Post -Uri $Api -Headers $Headers -ContentType "application/json" -Body $Body
$Booking.id

do {
    Start-Sleep -Seconds 30
    $Now = Invoke-RestMethod -Uri "$Api/$($Booking.id)" -Headers $Headers
    "$($Now.status) $($Now.step)"
} while ($Now.status -notin "ready", "failed", "cancelled", "done")

if ($Now.status -eq "ready") {
    [IO.File]::WriteAllText("$Dir\cert.pub", $Now.certificate + "`n")
    $Port = $Now.ssh_port
    "Ready until $($Now.ends_at). SSH port $Port. Public ports: $($Now.public_ports -join ', ')"
} else {
    "Not ready: $($Now.status) $($Now.error)"
}
```

Connect, or run one command:

```powershell
ssh -p $Port -i gpukey -o CertificateFile=cert.pub -o StrictHostKeyChecking=accept-new agent@{{HOST}}
ssh -p $Port -i gpukey -o CertificateFile=cert.pub -o StrictHostKeyChecking=accept-new agent@{{HOST}} nvidia-smi -L
```

Copy files in and out:

```powershell
scp -P $Port -i gpukey -o CertificateFile=cert.pub -r .\myproject agent@{{HOST}}:
scp -P $Port -i gpukey -o CertificateFile=cert.pub -r agent@{{HOST}}:results .
```

Check the time left, get more time, or end early:

```powershell
(Invoke-RestMethod -Uri "$Api/$($Booking.id)" -Headers $Headers).seconds_left

$More = Invoke-RestMethod -Method Post -Uri "$Api/$($Booking.id)/extend" -Headers $Headers -ContentType "application/json" -Body '{"hours": 1}'
[IO.File]::WriteAllText("$Dir\cert.pub", $More.certificate + "`n")

Invoke-RestMethod -Method Delete -Uri "$Api/$($Booking.id)" -Headers $Headers
```

## Linux or macOS

```bash
TOKEN="{{TOKEN}}"
API="{{URL}}/api/bookings"
ssh-keygen -q -t ed25519 -N "" -f gpukey

curl -s -X POST "$API" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"hours\": 1, \"gpus\": 2, \"purpose\": \"describe your job\", \"ssh_public_key\": \"$(cat gpukey.pub)\"}"
```

Use the returned `id`. Check every 30 seconds until `status` is `ready`:

```bash
curl -s "$API/BOOKING_ID" -H "Authorization: Bearer $TOKEN"
```

Save the `certificate` value as `cert.pub`, then connect:

```bash
ssh -p SSH_PORT -i gpukey -o CertificateFile=cert.pub -o StrictHostKeyChecking=accept-new agent@{{HOST}}
```

End early, or add time and save the new `certificate` as `cert.pub`:

```bash
curl -s -X DELETE "$API/BOOKING_ID" -H "Authorization: Bearer $TOKEN"
curl -s -X POST "$API/BOOKING_ID/extend" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"hours": 1}'
```

## Good to know

- You see a warning in your terminal 10 minutes before the end. At the end, your login stops
  and the VM resets.
- Extending works only while the booking is `ready` and nobody else is waiting.
- Run long jobs in `tmux`, so they keep running if your SSH connection drops.
- Downloads from the internet are usually faster than copying big files in over SSH.

## Errors

| Code | Meaning |
|---|---|
| 401 | Token is wrong or was replaced; ask your user for a new file |
| 409 | Not possible right now, for example someone else is waiting when you extend |
| 422 | Invalid request; check `hours`, `purpose` and the SSH key |
| 429 | Limit reached: {{MAX_OPEN}} open bookings, or {{DAILY_HOURS}} hours per 24 hours |
