# iSCSI CLI

CLI commands for retrieving and managing iSCSI target, initiator, image, and metrics information from the cluster.

---

## Command Overview

```text
iscsi
│
├── get
    ├── nodes
    ├── node
    ├── luns
    ├── tpgts
    ├── images
    ├── metrics
    ├── sessions
    └── errors

```

---

## Available Commands

| Command              | Description                                     |
| -------------------- | ----------------------------------------------- |
| `iscsi get nodes`    | List all discovered iSCSI target nodes          |
| `iscsi get node`     | Show summary information for a specific node    |
| `iscsi get luns`     | List LUNs configured on target nodes            |
| `iscsi get tpgts`    | Display Target Portal Groups (TPGTs)            |
| `iscsi get images`   | Show projected RootFS and PE images             |
| `iscsi get metrics`  | Retrieve iSCSI metrics and statistics           |
| `iscsi get sessions` | Show initiator sessions and mount status        |
| `iscsi get errors`   | Scan recent logs for storage and network errors |

---

# get nodes

### Syntax

```bash
iscsi get nodes [--label LABEL] [--json]
```

### Description

Lists all target nodes matching the provided Kubernetes label selector.

### Options

| Flag      | Description                  | Default             |
| --------- | ---------------------------- | ------------------- |
| `--label` | Target node label selector   | `iscsi-target=true` |
| `--json`  | Return output in JSON format | Disabled            |

### Example

```bash
iscsi get nodes --label "iscsi-target=true"
```

### Sample Output

```text
Nodes matching iscsi-target=true: 2

- node-1
- node-2
```

---

# get node

### Syntax

```bash
iscsi get node --name NODE [--base-path BASE_PATH] [--json]
```

### Description

Displays a summary of iSCSI configuration present on a target node.

### Options

| Flag          | Description                     | Default                           |
| ------------- | ------------------------------- | --------------------------------- |
| `--name`      | Target node name                | Required                          |
| `--base-path` | iSCSI target configuration path | `/sys/kernel/config/target/iscsi` |
| `--json`      | Return output in JSON format    | Disabled                          |

### Example

```bash
iscsi get node --name node-1
```

### Sample Output

```text
Node: node-1
Role: target
IQNs: 1
LUNs: 4
```

---

# get luns

### Syntax

```bash
iscsi get luns [--name NODE] [--base-path BASE_PATH] [--json]
```

### Description

Lists all configured LUNs and their associated images.

### Example

```bash
iscsi get luns --name node-1
```

### Sample Output

```text
IQN                      TPGT     LUN   Type     Image
----------------------------------------------------------
iqn.example.demo         tpgt_1   0     rootfs   image-1
iqn.example.demo         tpgt_1   1     pe       image-2
```

---

# get tpgts

### Syntax

```bash
iscsi get tpgts [--name NODE] [--base-path BASE_PATH] [--json]
```

### Description

Displays Target Portal Groups configured on target nodes.

### Example

```bash
iscsi get tpgts --name node-1
```

### Sample Output

```text
IQN                      TPGT     LUNs   ACLs
------------------------------------------------
iqn.example.demo         tpgt_1   4      2
```

---

# get images

### Syntax

```bash
iscsi get images [--name NODE] [--base-path BASE_PATH] [--json]
```

### Description

Lists projected RootFS and PE images attached to target nodes.

### Example

```bash
iscsi get images --name node-1
```

### Sample Output

```text
Node: node-1
Role: target
Images: 2

Image Name     LUN     Type
--------------------------------
image-1        0       rootfs
image-2        1       pe
```

---

# get metrics

### Syntax

```bash
iscsi get metrics \
    [--name NODE] \
    [--base-path BASE_PATH] \
    [--state-file FILE] \
    [--initiator-selector SELECTOR] \
    [--json] \
    [--no-state-update] \
    [--reset-state]
```

### Description

Collects iSCSI metrics from target and initiator nodes.

Metrics include:

- Target count
- Initiator count
- Image count
- Read MB
- Write MB
- IOPS
- Deleted image tracking
- Session information

### Example

```bash
iscsi get metrics --reset-state
```

### Sample Output

```text
SCSI Metrics
================================================================================================
State file: /home/iscsi/.cache/iscsi-metrics/state.json
Target nodes: worker-node-1

Configured iSCSI target nodes
- worker-node-1: iqn.2026-04.lab.local:lab.target01

Projected image summary
Node          | Targets                            | TPGTs | LUNs | Total | Rootfs | PE | Deleted Rootfs | Deleted PE
--------------+------------------------------------+-------+------+-------+--------+----+----------------+-----------
worker-node-1 | iqn.2026-04.lab.local:lab.target01 | 1     | 10   | 10    | 2      | 8  | 0              | 0

Cluster totals: total=10, rootfs=2, pe=8, deleted_rootfs=0, deleted_pe=0

LUN level read I/O metrics per worker node
worker-node-1
IQN                                | TPGT   | LUN   | Type   | Image            | udev_path                             | Read MBytes | Read IOPs
-----------------------------------+--------+-------+--------+------------------+---------------------------------------+-------------+----------
iqn.2026-04.lab.local:lab.target01 | tpgt_1 | lun_0 | rootfs | rootfs_disk1.img | /var/lib/iscsi_disks/rootfs_disk1.img | 46          | 102
iqn.2026-04.lab.local:lab.target01 | tpgt_1 | lun_1 | rootfs | rootfs_disk2.img | /var/lib/iscsi_disks/rootfs_disk2.img | 0           | 131
iqn.2026-04.lab.local:lab.target01 | tpgt_1 | lun_2 | pe     | pe_disk1.img     | /var/lib/iscsi_disks/pe_disk1.img     | 0           | 131
iqn.2026-04.lab.local:lab.target01 | tpgt_1 | lun_3 | pe     | pe_disk2.img     | /var/lib/iscsi_disks/pe_disk2.img     | 0           | 131
iqn.2026-04.lab.local:lab.target01 | tpgt_1 | lun_4 | pe     | pe_disk3.img     | /var/lib/iscsi_disks/pe_disk3.img     | 0           | 131
iqn.2026-04.lab.local:lab.target01 | tpgt_1 | lun_5 | pe     | pe_disk4.img     | /var/lib/iscsi_disks/pe_disk4.img     | 0           | 131
iqn.2026-04.lab.local:lab.target01 | tpgt_1 | lun_6 | pe     | pe_disk5.img     | /var/lib/iscsi_disks/pe_disk5.img     | 0           | 131
iqn.2026-04.lab.local:lab.target01 | tpgt_1 | lun_7 | pe     | pe_disk6.img     | /var/lib/iscsi_disks/pe_disk6.img     | 0           | 131
iqn.2026-04.lab.local:lab.target01 | tpgt_1 | lun_8 | pe     | pe_disk7.img     | /var/lib/iscsi_disks/pe_disk7.img     | 0           | 131
iqn.2026-04.lab.local:lab.target01 | tpgt_1 | lun_9 | pe     | pe_disk8.img     | /var/lib/iscsi_disks/pe_disk8.img     | 0           | 96

Detected storage/network errors
No storage/network errors detected.

Deleted PE/rootfs images since the last run
None
```

---

# get sessions

### Syntax

```bash
iscsi get sessions [--name NODE] [--label LABEL] [--json]
```

### Description

Displays active initiator sessions and mount status.

### Example

```bash
iscsi get sessions
```

### Sample Output

```text
Node: init-node-1
Role: initiator
Sessions: 3

Session details:
- tcp: [1] 10.0.0.10:3260,1 iqn.example:disk1
- tcp: [2] 10.0.0.11:3260,1 iqn.example:disk2

Node: init-node-2
Role: initiator
Sessions: 1

Session details:
- tcp: [1] 10.0.0.12:3260,1 iqn.example:disk3

```

---

# get errors

### Syntax

```bash
iscsi get errors [--name NODE] [--label LABEL] [--lines LINES] [--json]
```

### Description

Scans recent logs on target nodes for storage, network, kernel, and iSCSI related errors.

### Example

```bash
iscsi get errors --name node-1 --lines 200
```

### Sample Output

```text
Node: node-1
Role: target
Lines: 200

Detected errors
Severity   | Source   | Message
-----------+----------+--------------------------------------------
warning    | iscsi    | connection lost to target
critical   | storage  | Buffer I/O error on dev sda

Recent Logs:
Jun 01 11:28:43 worker-node-3 sshd[14304]: pam_unix(sshd:session): session closed for user iscsi
Jun 01 11:28:43 worker-node-3 systemd-logind[699]: Session 98 logged out. Waiting for processes to exit.
.
.
.
```

---

## Output Formats

The CLI supports:

- Human-readable output (default)
- JSON output (`--json`)

Example:

```bash
iscsi get nodes --json
```
