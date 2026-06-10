# iSCSI CLI

CLI commands for retrieving and managing iSCSI target, initiator, image, and metrics information from the cluster.

---

## Command Overview
### Target Node Commands

```text
iscsi
└── get
    ├── nodes
    ├── luns
    ├── tpgts
    ├── images
    ├── metrics

iscsi
└── describe
    └── node
```

### Initiator Node Commands

```text
iscsi
└── get
    ├── sessions
    └── mount-status
```
---

## Available Commands

| Command                          | Description                                             |
| -------------------------------- | ------------------------------------------------------- |
| `iscsi get nodes`                | List all discovered iSCSI target nodes                  |
| `iscsi describe node`            | Show detailed information for a specific target node    |
| `iscsi get luns`                 | List LUNs configured on target nodes                    |
| `iscsi get tpgts`                | Display Target Portal Groups (TPGTs)                    |
| `iscsi get images`               | Show projected RootFS and PE images                     |
| `iscsi get metrics`              | Retrieve read metrics and IOPS statistics per LUN       |
| `iscsi get sessions`             | Show detailed initiator session information             |
| `iscsi get mount-status`         | Show mount status of projected disks on initiator nodes |

---

# Target Node Commands


# get nodes

### Syntax

```bash
iscsi get nodes [--json]
```

### Description

Lists all target nodes using the configured default Kubernetes label selector.

### Example

```bash
iscsi get nodes
```

### Sample Output

```text

- ncn-w001
- ncn-w002
```

---

# describe node

### Syntax

```bash
iscsi describe node --name NODE_NAME [--json]
```

### Description

Displays a detailed summary of iSCSI configuration present on a target node.

The summary includes:

* IQNs
* TPGTs
* LUNs
* RootFS images
* PE images

### Options

| Flag          | Description                     | Default                           |
| ------------- | ------------------------------- | --------------------------------- |
| `--name`      | Target node name                | Required                          |
| `--json`      | Return output in JSON format    | Disabled                          |

### Example

```bash
iscsi describe node --name ncn-w001
```

### Sample Output

```text
Node: ncn-w001
Role: target

IQNs: 1
TPGTs: 1
LUNs: 10
Images: 10

IQNs
------------------------------------------------
iqn.2026-04.lab.local:lab.target01

TPGTs
------------------------------------------------
tpgt_1

LUN Summary
------------------------------------------------
LUN   Type     Image
0     rootfs   rootfs_disk1.img
1     rootfs   rootfs_disk2.img
2     pe       pe_disk1.img
3     pe       pe_disk2.img
4     pe       pe_disk3.img
5     pe       pe_disk4.img
6     pe       pe_disk5.img
7     pe       pe_disk6.img
8     pe       pe_disk7.img
9     pe       pe_disk8.img

Count of rootfs images: 2
Count of PE images: 8 
```

---

# get luns

### Syntax

```bash
iscsi get luns \
    [--name NODE_NAME] \
    [--type TYPE] \
    [--json]
```

### Description

Lists all configured LUNs and their associated images.

### Options

| Flag     | Description                |
| -------- | -------------------------- |
| `--type` | Filter by `rootfs` or `pe` |

### Example

```bash
iscsi get luns --name ncn-w001 --type pe
```

### Sample Output

```text
IQN                                TPGT     LUN   Type   Image
-----------------------------------------------------------------------
iqn.2026-04.lab.local:lab.target01 tpgt_1   2     pe     pe_disk1.img
iqn.2026-04.lab.local:lab.target01 tpgt_1   3     pe     pe_disk2.img
```

---

# get tpgts

### Syntax

```bash
iscsi get tpgts [--name NODE_NAME] [--json]
```

### Description

Displays Target Portal Groups configured on target nodes.

### Example

```bash
iscsi get tpgts --name ncn-w001
```

### Sample Output

```text
IQN                                TPGT     LUNs  
-------------------------------------------------
iqn.2026-04.lab.local:lab.target01 tpgt_1   10     
```

---

# get images

### Syntax

```bash
iscsi get images \
    [--name NODE_NAME] \
    [--type TYPE] \
    [--json]
```

### Description

Lists projected RootFS and PE images attached to target nodes.

### Options

| Flag     | Description                |
| -------- | -------------------------- |
| `--type` | Filter by `rootfs` or `pe` |

### Example

```bash
iscsi get images --name ncn-w001 --type rootfs
```

### Sample Output

```text
Node: ncn-w001
Role: target

Image Name          LUN     Type
-----------------------------------------
rootfs_disk1.img    0       rootfs
rootfs_disk2.img    1       rootfs
```

---

# get metrics

### Syntax

```bash
iscsi get metrics \
    [--name NODE_NAME] \
    [--state-file FILE] \
    [--json] \
    [--no-state-update] \
    [--reset-state]
```

### Description

Collects read metrics from target nodes.

Metrics include:

* Read MBytes
* Read IOPS

### Example

```bash
iscsi get metrics --name ncn-w001
```

### Sample Output

```text
Read Metrics Per LUN

Node: ncn-w001

LUN   Type     Image              Read MBytes   Read IOPs
----------------------------------------------------------------
lun_0 rootfs   rootfs_disk1.img   46            102
lun_1 rootfs   rootfs_disk2.img   0             131
lun_2 pe       pe_disk1.img       0             131
lun_3 pe       pe_disk2.img       0             131
lun_4 pe       pe_disk3.img       0             131
lun_5 pe       pe_disk4.img       0             131
lun_6 pe       pe_disk5.img       0             131
lun_7 pe       pe_disk6.img       0             131
lun_8 pe       pe_disk7.img       0             131
lun_9 pe       pe_disk8.img       0             96
```

---

# Initiator Node Commands


# get sessions

### Syntax

```bash
iscsi get sessions [--name NODE_NAME] [--json]
```

### Description

Displays complete iSCSI initiator session information using the output from:

```bash
iscsiadm -m session
```

### Example

```bash
iscsi get sessions --name ncn-w002
```

### Sample Output

```text
Node: ncn-w002
Role: initiator

tcp: [1] 10.0.0.10:3260,1 iqn.2026-04.lab.local:lab.target01
tcp: [2] 10.0.0.11:3260,1 iqn.2026-04.lab.local:lab.target02
```

---

# get mount-status

### Syntax

```bash
iscsi get mount-status [--name NODE_NAME] [--json]
```

### Description

Displays mounted iSCSI disks and mount status on initiator nodes.

### Example

```bash
iscsi get mount-status --name ncn-w002
```

### Sample Output

```text
Node: ncn-w002
Role: initiator

Device        Mount Point         Status
-------------------------------------------
/dev/sdb      /mnt/rootfs1         mounted
/dev/sdc      /mnt/pe1             mounted
```

---

## Output Formats

The CLI supports:

* Human-readable output (default)
* JSON output (`--json`)

Example:

```bash
iscsi get nodes --json
```
