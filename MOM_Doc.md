Date: 19-02-2026

Explore the following topics:
1) iSCSI and LUN concepts
2) kubernetes
3) OS boot procedure
4) s3fs
5) PXE boot, network boot, NFS boot
6) rootfs and PE images
7) Check about lab systems availability for the project setup
8) Read/Write IO

---

Date: 25-02-2026

Project:
1. Explore and configure kubernetes
2. Create kubernetes worker nodes & master nodes locally in a VM locally
  - Configure iSCSI target & initiator in this cluster
  - 4 worker nodes & 2 master nodes
  - Couple of iSCSI clients and initiator

Explore on:
1. How to configure iSCSI target and initiator 
2. How to establish session between iSCSI target and initiator
3. How to configure LUNs on the target
4. How to configure LUNs on the initator
5. How to send I/Os to the LUNs on the initiator

Read articles, wikis and documentation instead of fully relying on chatGPT

Objective: To understand the concepts in-depth so that we can make design decisions confidently

---

Date: 04-03-2026

Project:
1. Explore and configure kubernetes
2. Create kubernetes worker nodes & master nodes locally in a VM locally
  - Configure iSCSI target & initiator in this cluster
  - 4 worker nodes & 2 master nodes
  - Couple of iSCSI clients and initiator

For learning purposes, configure kubernetes cluster locally 
5 nodes:
- 1 master + 4 worker nodes
- Retrieve data from the master node
- Configure 2 worker nodes as iSCSI targets
- Configure 2 worker nodes as iSCSI clients

Simultaneously also try to setup lab systems as soon as possible

Lab Systems Config:
- 4 systems in the k8s cluster
- Configure them as worker and master nodes
- Minimum 2 systems
- To simulate failover atleast 4 systems are needed
- Base OS - Linux SLES Latest version installed

Resources: 
- Configuring iSCSI in Rocky Linux (https://reintech.io/blog/configuring-iscsi-initiator-target-rocky-linux-9)

---

Date: 15-04-2026

1. Insights
- Automating creation of target and client iSCSI
- create 5-10 LUNs with size of less than 50 mb
- add description of software downloaded, commands used for installing and configuring iscsi target and client
- give info about each person's contribution
- just use VMs on lab systems. If able to get it to work on 4 systems. Try to get it to work on lab systems.
- optimize the script to show more metrics. Find important metrics to show. 
- CLI is priority over UI

2. Action items

- finalize list of metrics to be retrieved. 
- mount LUNs on client node
- issue i/o's from the luns on the client system. observe metrics on corresponding luns of target system
- map target client luns
- add Kubernetes label to iscsi targets only
- application should only retrieve metrics from labeled nodes
- create general worker nodes to differentiate labeled iscsi targets and unlabeled
- count number of targets
- create luns using empty/dummy disk files

3. Metrics list

- LUNwise -(4 items+ read_mbytes,write_mbytes,num_cmds(iops))
- count number of targets configured and labeled as iscsi targets
- count number of targets not configured/labeled as iscsi targets
- give metrics of types of images in each worker node
- total number of targets
- network metrics

---

Date: 22/04/26

Action Items:
- Create atleast 2 iscsi-target VMs and name them as iscsi-target-1 and iscsi-target-2
- Create all the fileio disk images in the host OS and mount the path to both the targets 
- Create only one tpg inside each target but the iqn should contain the vm's hostname
- Use the default port 3260 everywhere 
- Modify the scripts to follow the above changes
- Optional : remove client iqn to simplify the setup

---

Date: 29/04/26

Important points:
- Mid demo is on May 11th
- Slides and demo video for presentation(20 mins minimum, max 40 mins)
- The entire project should be completed by May end. At max by June 1st week
- Prepare slide deck and share by May 4th-5th

Slides should include:
- First slide - Title having team members name list 
- Agenda - list of items 
- Inro - what is the project. ?  goal ? 
- Architecture - block diagram 
- iscsi target/initiator/ luns configuration etc.. 
- Tell about your application (metrics)
- What you learnt and next action items 
- Demo video

---

Date: 13/05/26
Mid Checkpoint Presentation

Feedback & Next Action Items:

- Enhance the CLI to retrieve iSCSI related data from the compute/initiator nodes as well -> Pracheeta
    - Mounted images
    - Unmounted images
- Add error reporting functionality to the CLI -> Sameer 
    - Look for errors in both iSCSI target and initiator nodes and report the same (How: look in dmesg (/var/log/message, /var/log/... ), beyond dmesg explore
- If time permits, explore continuous monitoring and reporting (using nodeexporter, prometheus & grafana (GUI)) -> Sasank & Devansh + Pracheeta (after first task)
    - Use text file collector
    - Reference: https://prometheus.io/docs/guides/node-exporter/
- Deadline till Mid-June 2026

- Optional: Try booting the client node using a boot image (using pixie boot)
- Note: Try to write code on your own










