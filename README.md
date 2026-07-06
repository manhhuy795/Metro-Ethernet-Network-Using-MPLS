# Metro Ethernet Network Using MPLS

Lab mô phỏng mạng Metro Ethernet MAN sử dụng MPLS để kết nối 3 chi nhánh doanh nghiệp qua hạ tầng ISP. Project được xây dựng trên Mininet, FRR, MPLS Linux kernel, OVS và dashboard HTML để theo dõi kết quả kiểm thử.

## Mục Tiêu

- Mô phỏng ISP/MPLS backbone gồm các router PE/P.
- Kết nối 3 chi nhánh với 3 kiến trúc LAN khác nhau.
- Kiểm chứng MPLS/LDP, OSPF, ECMP, VLAN, inter-VLAN routing và VPLS/L2VPN.
- Đo hiệu năng mạng: throughput, delay, packet loss, jitter.
- Sinh JSON kết quả và hiển thị bằng dashboard web.
- So sánh MPLS label switching với IP routing baseline.

## Kiến Trúc Mạng

| Khu vực | Thành phần | Mô tả |
| --- | --- | --- |
| ISP/MPLS Backbone | PE1, PE2, PE3, P1, P2, P3, P4 | Core MPLS dùng LDP/LFIB để chuyển tiếp nhãn |
| Chi nhánh 1 | CE1, flat switch, host1..host4 | Flat Network, toàn bộ host cùng subnet |
| Chi nhánh 2 | CE2, core/distribution/access switches | Mô hình 3 lớp, VLAN 10/20/30, inter-VLAN routing |
| Chi nhánh 3 | CE3, spine/leaf switches | Leaf-Spine, OSPF underlay và ECMP |
| Dashboard | `mpls_dashboard_tool.html` | Đọc JSON từ `mpls_results/latest.json` hoặc API local |

## Cấu Trúc Project

```text
.
├── topology.py                 # Dựng topology Mininet/MPLS/OVS/FRR
├── runner.py                   # Bộ lệnh verify, runquick, runall, trace, dashboard
├── analyze.py                  # Phân tích kết quả và sinh báo cáo
├── branch1.py                  # Cấu hình Chi nhánh 1 - Flat Network
├── branch2.py                  # Cấu hình Chi nhánh 2 - Core/Distribution/Access
├── branch3.py                  # Cấu hình Chi nhánh 3 - Leaf-Spine
├── mpls_dashboard_tool.html    # Dashboard web
├── run_dashboard.py            # HTTP server/API cho dashboard
├── network_design.json         # Metadata thiết kế mạng
├── dashboard_data.json         # Dữ liệu mẫu cho dashboard
├── setup.sh                    # Cài đặt môi trường lab
└── README_RUN.txt              # Ghi chú chạy lab dạng text
```

## Yêu Cầu Môi Trường

Khuyến nghị chạy trên Ubuntu hoặc WSL Ubuntu có quyền `sudo`.

Cần có:

- Python 3
- Mininet
- Open vSwitch
- FRR/vtysh
- iperf3
- Linux MPLS kernel modules
- Trình duyệt để mở dashboard

Các thành phần này có thể được cài/cấu hình bằng script:

```bash
sudo bash setup.sh
```

## Cách Chạy Nhanh

1. Dọn Mininet cũ:

```bash
sudo mn -c
```

2. Khởi động topology:

```bash
sudo python3 topology.py
```

Sau khi chạy xong, terminal sẽ vào Mininet CLI:

```text
mininet>
```

3. Kiểm tra nhanh topology:

```text
verify
```

4. Sinh dữ liệu nhanh cho dashboard:

```text
runquick
```

5. Chạy bộ đo đầy đủ và so sánh MPLS/IP:

```text
runall
```

`runall` sẽ chạy test ở MPLS mode, chuyển tạm sang IP routing baseline, chạy lại cùng bộ test, rồi gom kết quả vào một file JSON.

## Dashboard

Trong Mininet CLI, chạy:

```text
dash 8000
```

Sau đó mở trình duyệt:

```text
http://127.0.0.1:8000/mpls_dashboard_tool.html
```

Dashboard đọc dữ liệu từ các nguồn sau:

```text
/api/latest
mpls_results/latest.json
mpls_results/dashboard_data.json
dashboard_data.json
```

Các file kết quả chính:

```text
mpls_results/latest.json
mpls_results/dashboard_data.json
dashboard_data.json
```

Lưu ý:

- Nên mở dashboard bằng URL `http://127.0.0.1:8000/...`, không nên mở bằng `file://` nếu muốn tự load JSON.
- Dashboard không tự reload dữ liệu.
- Sau khi chạy lại `runquick` hoặc `runall`, bấm **Nạp lại dữ liệu** trên dashboard.
- Nếu muốn bỏ dữ liệu cũ trong trình duyệt, bấm **Xóa dữ liệu lưu**.

API kiểm tra nhanh:

```text
http://127.0.0.1:8000/api/files
http://127.0.0.1:8000/api/latest
```

## Lệnh Kiểm Chứng Quan Trọng

### MPLS / LDP / LFIB

```text
P1 v show mpls ldp binding
P1 v show mpls ldp neighbor
P1 ip -f mpls route
```

Các lệnh này dùng để chứng minh router core có LDP binding, LDP neighbor và LFIB MPLS.

### OSPF Và ECMP Ở Chi Nhánh 3

```text
spine1 v show ip ospf neighbor
spine1 v show ip ospf route
CE3 v show ip route
spine1 ip route
```

Trong output của `spine1 ip route`, các dòng có nhiều `nexthop ... weight 1` là bằng chứng ECMP.

### VLAN Và Inter-VLAN Routing Ở Chi Nhánh 2

```text
CE2 sh -c "ip -brief addr show | grep ce2-lan"
CE2 ip route
CE2 v show ip route
H2_1 ping -c 2 192.168.21.11
```

Nếu `H2_1` ping được `192.168.21.11` với `0% packet loss`, inter-VLAN routing qua CE2 hoạt động đúng.

Không dùng lệnh sau vì `iproute2` có thể báo lỗi:

```text
CE2 ip -brief addr show ce2-lan.10 ce2-lan.20 ce2-lan.30
```

### VPLS / L2VPN

```text
PE1 ip link show type gretap
PE1 bridge fdb show dev br-vpls
```

### OVS / VLAN

```text
core1 ovs-vsctl show
```

Output cần thấy bridge/port của `core1`, `dist1`, `dist2`, `access1`, `access2`, `access3` và các VLAN 10, 20, 30.

## Trace MPLS Và Hex Dump

Dashboard có tab **Mô tả luồng di chuyển** để trình bày quá trình:

- PUSH tại PE ingress.
- SWAP/FORWARD tại P router trung gian.
- PHP tại P router gần PE đích.
- POP tại PE egress.
- Đối chiếu bằng `tcpdump -XX`/hex dump trong JSON.

Lệnh demo:

```text
trace H1_1 H2_1
trace H1_1 H3_1
trace H2_1 H3_5
```

Gợi ý luồng dễ thấy cả SWAP và PHP:

- Branch 1 -> Branch 3: `host1 -> web1`, đường `PE1 -> P1 -> P2 -> PE3`.
- Branch 3 -> Branch 1: `web1 -> host1`, đường `PE3 -> P2 -> P1 -> PE1`.

Cách đọc nhanh:

- `0x8847`: EtherType MPLS.
- `16001/16002/16003`: transport label theo PE đích.
- `101/201/301`: service label theo chi nhánh đích.
- Sau PHP, outer transport label thường biến mất.
- Sau POP tại PE egress, frame ra CE trở lại IPv4/ICMP.

## Quy Ước Subnet Backbone

Backbone sử dụng dải `10.255.x.0/30` cho các liên kết point-to-point trong vùng ISP/MPLS. Giá trị `x` là mã liên kết giữa router.

| Link | Subnet |
| --- | --- |
| CE1-PE1 | `10.0.11.0/30` |
| CE2-PE2 | `10.0.12.0/30` |
| CE3-PE3 | `10.0.13.0/30` |
| PE1-P1 | `10.255.11.0/30` |
| P1-P2 | `10.255.12.0/30` |
| PE1-P3 | `10.255.13.0/30` |
| P1-P4 | `10.255.14.0/30` |
| PE2-P3 | `10.255.23.0/30` |
| PE2-P4 | `10.255.24.0/30` |
| PE3-P2 | `10.255.32.0/30` |
| PE3-P4 | `10.255.34.0/30` |
| P3-P4 | `10.255.43.0/30` |
| P1-P3 | `10.255.103.0/30` |
| P2-P3 | `10.255.203.0/30` |
| P2-P4 | `10.255.204.0/30` |

Các prefix `10.255.103.0/30`, `10.255.203.0/30`, `10.255.204.0/30` và `10.255.43.0/30` được đặt có chủ ý để tránh trùng prefix PE-P. Không nên đổi nếu chưa đồng bộ lại `topology.py`, route và MPLS LFIB.

## Quy Trình Khuyến Nghị Khi Báo Cáo

1. Khởi động topology:

```bash
sudo mn -c
sudo python3 topology.py
```

2. Trong Mininet CLI, kiểm tra nhanh:

```text
verify
```

3. Chạy đầy đủ bộ đo:

```text
runall
```

4. Mở dashboard:

```text
dash 8000
```

5. Truy cập:

```text
http://127.0.0.1:8000/mpls_dashboard_tool.html
```

6. Bấm **Nạp lại dữ liệu** và chụp các tab:

- Tổng quan
- Thiết kế chi nhánh
- Kiểm chứng yêu cầu
- Kết quả & biểu đồ
- So sánh MPLS/IP
- Mô tả luồng di chuyển
- MPLS / OSPF / VPLS

## Xử Lý Lỗi Thường Gặp

### Dashboard hiện N/A hoặc chưa có dữ liệu

Chạy lại trong Mininet CLI:

```text
runquick
```

hoặc:

```text
runall
```

Sau đó bấm **Nạp lại dữ liệu** trên dashboard.

### Không thấy result JSON

Kiểm tra file:

```text
sh ls -l mpls_results/latest.json dashboard_data.json
```

Kiểm tra API:

```text
http://127.0.0.1:8000/api/files
```

Trong `/api/files`, `latest_candidates` nên có:

```text
mpls_results/latest.json
```

### Dashboard vẫn đọc dữ liệu cũ

1. Bấm **Xóa dữ liệu lưu**.
2. Chạy lại:

```text
dash 8000
```

3. Mở lại dashboard và bấm **Nạp lại dữ liệu**.

## Thoát Lab

Trong Mininet CLI:

```text
exit
```

Sau đó dọn Mininet:

```bash
sudo mn -c
```

## Ghi Chú

- Các file trong `mpls_results/` là output sinh tự động sau khi chạy lab.
- `dashboard_data.json` ở thư mục gốc được dùng như dữ liệu mẫu để dashboard có thể demo nhanh.
- Nếu thay đổi subnet backbone, cần đồng bộ trong topology, route, LFIB/MPLS và dashboard metadata.
pyenv install -l | grep "  3.12"
pyenv install 3.12.8
pyenv shell 3.12.8

python -m venv ~/ryu312-venv
source ~/ryu312-venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install ryu pyyaml

ryu-manager --version
