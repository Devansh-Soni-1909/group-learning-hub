

What is iSCSI?

iSCSI (Internet Small Computer System Interface) is a protocol that allows block-level storage to be transmitted over TCP/IP networks.

It enables:

A remote disk (storage server)
to appear as

A local disk (e.g., /dev/sdb) on a client system

Unlike file-based storage (like NFS), iSCSI works at the block level, meaning the operating system treats it as a real physical disk.



Works over standard IP networks

Official References I have used:

IETF iSCSI RFC: https://datatracker.ietf.org/doc/html/rfc3720

Open-iSCSI Documentation: https://github.com/open-iscsi/open-iscsi



2. Architecture Overview

3. <img width="899" height="514" alt="Screenshot 2026-03-02 213505" src="https://github.com/user-attachments/assets/6289b043-b6a6-44fa-b3ce-f21738d25201" />



4. Key Concepts
3.1 Backstore

A backstore is the actual storage backing device.

It can be:

File

Physical disk

LVM

RAM disk

In this lab, I have used  a file-based backstore.

3.2 LUN (Logical Unit Number)

A LUN is a logical disk exported to the initiator.

Think of a LUN as:

A virtual hard disk exposed over network.

3.3 IQN (iSCSI Qualified Name)

Unique identifier for iSCSI target or initiator.

Format:

iqn.YYYY-MM.reverse-domain:name

Example:

iqn.2026-03.local.lab:target1
4. Lab Environment

OS: Ubuntu (Single VM setup)

Target and Initiator configured on same machine (loopback)

Portal IP: 127.0.0.1

5. Installation

Update system:

sudo apt update

Install required packages:

sudo apt install targetcli-fb open-iscsi -y
Explanation

targetcli-fb → Configures iSCSI target

open-iscsi → Initiator software

6. Target Configuration (Storage Server Side)

Start interactive shell:

sudo targetcli
Step 1: Create Backstore
/backstores/fileio create disk1 /root/disk1.img 2G

Meaning:

Creates a 2GB file

File behaves as virtual disk

Verify:

/backstores/fileio ls
Step 2: Create Target
/iscsi create iqn.2026-03.local.lab:target1

Verify:

/iscsi ls
Step 3: Create LUN
/iscsi/iqn.2026-03.local.lab:target1/tpg1/luns create /backstores/fileio/disk1

Now disk1 is exported as LUN0.

Step 4: Disable Authentication (Lab Purpose Only)
/iscsi/iqn.2026-03.local.lab:target1/tpg1 set attribute authentication=0
Step 5: Create ACL

Allow initiator access:

/iscsi/iqn.2026-03.local.lab:target1/tpg1/acls create iqn.2026-03.local.lab:client1
Step 6: Save and Exit
saveconfig
exit

Target configuration complete 

7. Initiator Configuration

Edit initiator name:

sudo nano /etc/iscsi/initiatorname.iscsi

Set:

InitiatorName=iqn.2026-03.local.lab:client1

Restart service:

sudo systemctl restart open-iscsi
8. Discover Target
sudo iscsiadm -m discovery -t sendtargets -p 127.0.0.1

Explanation:

discovery → Find available targets

sendtargets → Discovery method

-p → Portal (IP address)

9. Login to Target
sudo iscsiadm -m node --login

Check session:

sudo iscsiadm -m session

If session appears → connection successful.

10. Verify New Disk
lsblk

You should see:

sdb   2G

That is your iSCSI LUN.

11. Send I/O to LUN

Format disk:

sudo mkfs.ext4 /dev/sdb

Create mount point:

sudo mkdir /mnt/iscsi

Mount disk:

sudo mount /dev/sdb /mnt/iscsi

Test I/O:

sudo touch /mnt/iscsi/file1
sudo echo "Hello iSCSI" | sudo tee /mnt/iscsi/test.txt
ls /mnt/iscsi



12. Create Multiple LUNs

Re-enter targetcli:

sudo targetcli

Create second disk:

/backstores/fileio create disk2 /root/disk2.img 1G

Attach as LUN:

/iscsi/iqn.2026-03.local.lab:target1/tpg1/luns create /backstores/fileio/disk2

Exit:

exit

Rescan from initiator:

sudo iscsiadm -m node --rescan

Verify:

lsblk

Now you should see:

sdb   2G
sdc   1G

