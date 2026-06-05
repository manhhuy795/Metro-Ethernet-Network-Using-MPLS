METRO ETHERNET MPLS LAB - README CHAY THU
=========================================

Muc tieu lab
------------
Lab mo phong Metro Ethernet MAN su dung MPLS de ket noi 3 chi nhanh doanh
nghiep qua ha tang ISP. Mo hinh gom:

- ISP/MPLS backbone: PE1, PE2, PE3 va P1, P2, P3, P4.
- Chi nhanh 1: Flat Network.
- Chi nhanh 2: Core - Distribution - Access voi VLAN 10/20/30.
- Chi nhanh 3: Leaf-Spine voi OSPF va ECMP.
- Do hieu nang: throughput, delay, packet loss, jitter.
- Dashboard HTML doc JSON tu mpls_results/latest.json.

============================================================
1. GIAI NEN VA VAO THU MUC
============================================================

unzip -o mpls_fixed_v20_dashboard_lock.zip
cd mpls_fixed_v20_dashboard_lock

Neu thu muc cua ban co ten khac thi cd vao dung thu muc vua giai nen.

============================================================
2. CHAY SETUP LAN DAU
============================================================

sudo bash setup.sh

setup.sh se cai/cau hinh cac thanh phan can thiet cho lab nhu Mininet, FRR,
MPLS kernel modules, iperf3 va wrapper vtysh an toan.

============================================================
3. DON MININET CU VA KHOI DONG TOPOLOGY
============================================================

sudo mn -c
sudo python3 topology.py

Sau khi topology khoi dong xong, ban se vao prompt:

mininet>

============================================================
4. CAC LENH CAN CHAY TRONG MININET CLI
============================================================

4.1. Xem danh sach tool ngan gon
---------------------------------

verify

4.2. Chay kiem tra nhanh toan bo control-plane/data-plane
--------------------------------------------------------

verify

4.3. Sinh JSON nhanh cho dashboard
----------------------------------

runquick

4.4. Chay bo do day du va so sanh MPLS/IP cho bao cao
------------------------------------------------------

runall

Lenh runall se chay day du bo test o MPLS mode, sau do tam thoi chuyen backbone sang IP routing baseline, chay lai cung bo test va gop ket qua vao cung mot file latest.json.

Sau khi chay runquick hoac runall, cac file JSON chinh se duoc tao tai. Rieng runall se co them truong compare de dashboard hien tab So sanh MPLS/IP:

mpls_results/latest.json
mpls_results/dashboard_data.json
dashboard_data.json

============================================================
5. MO DASHBOARD DUNG CACH
============================================================

Trong mininet>, chay:

dash 8000

Sau do mo trinh duyet:

http://127.0.0.1:8000/mpls_dashboard_tool.html

Luu y quan trong:
- Khong nen mo dashboard bang file:// neu muon tu doc JSON.
- Ban nen mo bang http://127.0.0.1:8000/...
- Ban v20 se tu restart port 8000 de tranh viec trinh duyet doc server cu.
- Dashboard khong tu dong reload JSON.
- Khi da load duoc 1 lan, dashboard giu nguyen du lieu do trong localStorage.
- Neu muon cap nhat ket qua moi sau khi chay runall/runquick, bam "Nap lai du lieu".
- Neu muon xoa du lieu cu da luu tren browser, bam "Xoa du lieu luu".

Kiem tra API JSON truc tiep tren trinh duyet neu can:

http://127.0.0.1:8000/api/files
http://127.0.0.1:8000/api/latest

Neu /api/latest bao chua co result JSON, hay chay lai trong mininet>:

runquick

hoac:

runall

roi bam "Nap lai du lieu" tren dashboard.


============================================================
5.1. QUY UOC DAT TEN SUBNET BACKBONE
============================================================

Backbone su dung dai 10.255.x.0/30 cho cac lien ket point-to-point trong
vung ISP/MPLS. Gia tri x la ma lien ket giua cac router, khong phai subnet
ngau nhien.

Quy uoc chung:
- 10.0.11.0/30, 10.0.12.0/30, 10.0.13.0/30: cac lien ket CE-PE.
- 10.255.11.0/30, 10.255.13.0/30, 10.255.23.0/30, ...: cac lien ket PE-P.
- Cac lien ket P-P duoc dat sao cho khong trung voi cac lien ket PE-P.

Mot so subnet P-P co dang dac biet de tranh trung:
- 10.255.103.0/30: P1-P3. Khong dung 10.255.13.0/30 vi da dung cho PE1-P3.
- 10.255.203.0/30: P2-P3. Khong dung 10.255.23.0/30 vi da dung cho PE2-P3.
- 10.255.204.0/30: P2-P4. Khong dung 10.255.24.0/30 vi da dung cho PE2-P4.
- 10.255.43.0/30: P3-P4. Khong dung 10.255.34.0/30 vi da dung cho PE3-P4,
  nen dung ma dao 43 de tranh trung.

Bang tom tat cac link backbone chinh:

CE1-PE1  10.0.11.0/30
CE2-PE2  10.0.12.0/30
CE3-PE3  10.0.13.0/30
PE1-P1   10.255.11.0/30
P1-P2    10.255.12.0/30
PE1-P3   10.255.13.0/30
P1-P4    10.255.14.0/30
PE2-P3   10.255.23.0/30
PE2-P4   10.255.24.0/30
PE3-P2   10.255.32.0/30
PE3-P4   10.255.34.0/30
P3-P4    10.255.43.0/30
P1-P3    10.255.103.0/30
P2-P3    10.255.203.0/30
P2-P4    10.255.204.0/30

Ghi chu: cac prefix 10.255.103/203/204/43 la co chu y de tranh trung prefix
PE-P. Khong nen doi cac prefix nay neu khong dong bo lai topology.py, route
va MPLS LFIB.

============================================================
6. LENH BAT BUOC THEO DE BAI / KIEM TRA CHINH
============================================================

6.1. Xem thong so dinh tuyen MPLS va LDP
----------------------------------------

Dang ngan gon khuyen dung:

P1 v show mpls ldp binding
P1 v show mpls ldp neighbor
P1 ip -f mpls route

Dang day du tuong duong:

P1 /tmp/frr-mpls-lab/P1/v show mpls ldp binding
P1 /tmp/frr-mpls-lab/P1/v show mpls ldp neighbor
P1 ip -f mpls route

6.2. Xem thong so noi bo OSPF va ECMP Branch 3
----------------------------------------------

Dang ngan gon khuyen dung:

spine1 v show ip ospf neighbor
spine1 v show ip ospf route
CE3 v show ip route
spine1 ip route

Dang day du tuong duong:

spine1 /tmp/frr-mpls-lab/spine1/v show ip ospf neighbor
spine1 /tmp/frr-mpls-lab/spine1/v show ip ospf route
CE3 /tmp/frr-mpls-lab/CE3/v show ip route
spine1 ip route

Trong output cua spine1 ip route, cac dong co nhieu "nexthop ... weight 1"
la bang chung ECMP.

6.3. Kiem tra Branch 2 VLAN va inter-VLAN routing
-------------------------------------------------

Lenh dung de xem subinterface VLAN tren CE2:

CE2 sh -c "ip -brief addr show | grep ce2-lan"

Khong dung lenh cu nay vi iproute2 se bao loi:

CE2 ip -brief addr show ce2-lan.10 ce2-lan.20 ce2-lan.30

Cac lenh Branch 2 can chay:

CE2 sh -c "ip -brief addr show | grep ce2-lan"
CE2 ip route
CE2 v show ip route
H2_1 ping -c 2 192.168.21.11

Dang day du tuong duong:

CE2 sh -c "ip -brief addr show | grep ce2-lan"
CE2 ip route
CE2 /tmp/frr-mpls-lab/CE2/v show ip route
H2_1 ping -c 2 192.168.21.11

Neu H2_1 ping duoc 192.168.21.11 voi 0% packet loss thi inter-VLAN routing
qua CE2 da hoat dong.

6.4. Phan tich L2VPN / VPLS Backbone
------------------------------------

PE1 ip link show type gretap
PE1 bridge fdb show dev br-vpls

6.5. Kiem dinh VLAN va mang switch ao OVS
----------------------------------------

core1 ovs-vsctl show

Output can thay cac bridge/port nhu core1, dist1, dist2, access1, access2,
access3 va cac tag/trunks VLAN 10, 20, 30.

============================================================
7. LENH NGOAI LE / HO TRO DEMO
============================================================

7.1. Trace duong MPLS mo phong label switching
----------------------------------------------

trace H1_1 H2_1
trace H1_1 H3_1
trace H2_1 H3_5

7.2. Xem route tren cac node khac
---------------------------------

route CE1
route CE2
route CE3
iproute spine1
mpls P1
mpls PE1

7.3. Test ping nhanh
--------------------

H1_1 ping -c 2 H2_1
H1_1 ping -c 2 H3_1
H2_1 ping -c 2 H3_5
H3_1 ping -c 2 H3_5

7.4. Dashboard helper
---------------------

dash 8000
dashstop
dashboard
json

============================================================
8. QUY TRINH KHUYEN NGHI DE BAO CAO
============================================================

Buoc 1: Khoi dong topology

sudo mn -c
sudo python3 topology.py

Buoc 2: Trong mininet>, kiem tra nhanh

verify
verify

Buoc 3: Tao du lieu dashboard

runall

Buoc 4: Mo dashboard

dash 8000

Mo trinh duyet:

http://127.0.0.1:8000/mpls_dashboard_tool.html

Buoc 5: Tren dashboard bam "Nap lai du lieu" de doc latest JSON.

Buoc 6: Kiem tra cac tab:

- Tong quan
- Thiet ke chi nhanh
- Kiem chung yeu cau
- Ket qua & bieu do
- So sanh MPLS/IP
- MPLS / OSPF / VPLS

============================================================
9. XU LY LOI DASHBOARD KHONG LOAD SO LIEU
============================================================

Neu dashboard hien design_only, no timestamp, N/A:

1. Dam bao da chay trong mininet>:

runquick

hoac:

runall

2. Kiem tra file result co ton tai khong:

sh ls -l mpls_results/latest.json dashboard_data.json

3. Restart dashboard server:

dash 8000

Ban v20 se tu kill server cu tren port 8000 roi start lai.

4. Mo URL debug:

http://127.0.0.1:8000/api/files

Trong api/files, latest_candidates phai co:

mpls_results/latest.json

5. Quay lai dashboard va bam:

Nap lai du lieu

Neu van khong duoc, chon JSON thu cong:

mpls_results/latest.json

============================================================
10. THOAT LAB
============================================================

Trong mininet>:

exit

Sau do tren terminal Ubuntu:

sudo mn -c

============================================================
9. MINH CHUNG PUSH/SWAP/PHP/POP BANG TCPDUMP HEX DUMP
============================================================

Ban nay co them tab dashboard: "Mo ta luong di chuyen".

Cach chay de co minh chung:

1) Trong Mininet CLI, chay nhanh:

runquick

Hoac chay day du cho bao cao:

runall

2) Mo dashboard:

dash 8000
http://127.0.0.1:8000/mpls_dashboard_tool.html

3) Bam "Nap lai du lieu".

4) Vao tab "Mo ta luong di chuyen", chon Branch nguon va Branch dich.
Dashboard se hien:
- Luong di chuyen qua CE/PE/P.
- PUSH tai PE ingress.
- SWAP/FORWARD tai P trung gian neu duong co nhieu P-hop.
- PHP tai P gan PE dich.
- POP tai PE egress.
- Tcpdump -XX / Hex Dump da duoc runner.py chup san va luu trong latest.json.

Goi y demo de thay ro ca SWAP va PHP:
- Branch 1 -> Branch 3: host1 -> web1, duong PE1 -> P1 -> P2 -> PE3.
- Branch 3 -> Branch 1: web1 -> host1, duong PE3 -> P2 -> P1 -> PE1.

Cach doc bang chung:
- EtherType 0x8847: frame MPLS.
- Label 16001/16002/16003: transport label theo PE dich.
- Label 101/201/301: service label theo chi nhanh dich.
- Sau PHP, outer transport label bien mat, thuong chi con service label.
- Sau POP tai PE egress, frame ra CE tro lai IPv4/ICMP va khong con MPLS.
