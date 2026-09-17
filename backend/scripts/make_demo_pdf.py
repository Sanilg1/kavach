"""Generate a small text-based Computer Networks study PDF for demos and tests.
Usage: python scripts/make_demo_pdf.py [out.pdf]"""
from __future__ import annotations

import sys
from pathlib import Path

from fpdf import FPDF

SECTIONS = [
    ("1 Network Fundamentals", [
        "A computer network is a set of devices connected by communication links that exchange data. The devices at the edge of the network are called hosts or end systems. Links can be wired, such as copper or optical fibre, or wireless.",
        "Data is sent as packets. A packet is a formatted unit of data with a header that carries control information and a payload that carries the actual data. Packet switching lets many conversations share the same link by interleaving packets.",
        "Two performance measures matter most: bandwidth, the number of bits that can be transmitted per second, and latency, the time a bit takes to travel from sender to receiver. Round trip time (RTT) is the time for a small packet to go to the receiver and back.",
    ]),
    ("2 The OSI Model", [
        "The OSI reference model divides network functions into seven layers: physical, data link, network, transport, session, presentation and application. Each layer offers services to the layer above and uses services of the layer below.",
        "Layering separates concerns. The physical layer moves raw bits over a medium, the data link layer moves frames between neighbours, the network layer routes packets between networks, and the transport layer delivers data between processes on the end hosts.",
        "Encapsulation means each layer adds its own header to the data it receives from the layer above. On the receiving side the headers are removed in reverse order.",
    ]),
    ("3 TCP/IP", [
        "The Internet uses the TCP/IP protocol suite, which is usually described with four layers: link, internet, transport and application. IP, the Internet Protocol, delivers packets between hosts on a best effort basis: packets can be lost, duplicated, delayed or reordered.",
        "Every host has an IP address. Routers forward packets towards their destination using routing tables. IP does not guarantee delivery; reliability, when needed, is provided by the transport layer.",
    ]),
    ("4 TCP versus UDP", [
        "The transport layer offers two main protocols. UDP, the User Datagram Protocol, is connectionless: it simply adds port numbers and a checksum to each datagram and sends it. UDP is lightweight and fast but offers no reliability, ordering or flow control.",
        "TCP, the Transmission Control Protocol, is connection oriented and reliable. It delivers a byte stream in order and without loss by numbering bytes with sequence numbers, acknowledging received data and retransmitting lost segments.",
        "UDP suits applications such as DNS lookups, live video and online games where low delay matters more than perfect delivery. TCP suits the web, email and file transfer where every byte must arrive.",
    ]),
    ("5 TCP Three-Way Handshake", [
        "Before TCP can transfer application data, the two end points must establish a connection. Each side must learn that the other side is alive and must agree on initial sequence numbers. Because a single request could be lost, TCP uses an exchange of three segments known as the three-way handshake.",
        "Step one: the client sends a segment with the SYN flag set and an initial sequence number x. Step two: the server replies with a segment that has both the SYN and ACK flags set; it acknowledges the client by setting the acknowledgement number to x plus one and supplies its own initial sequence number y.",
        "Step three: the client sends an ACK segment whose acknowledgement number is y plus one. At this point both sides have sent and received an acknowledgement, and the connection is established. The third ACK confirms receipt of the server's SYN-ACK; it does not encrypt anything or resolve names.",
        "The state kept by each side includes the sequence numbers, receive buffers and window sizes. Closing a connection uses a similar exchange with FIN segments.",
    ]),
    ("6 Flow Control", [
        "Flow control prevents a fast sender from overwhelming a slow receiver. TCP uses a sliding window: the receiver advertises a receive window, the number of bytes it can still accept, in every segment it sends.",
        "The sender may have at most one window of unacknowledged bytes in flight. As acknowledgements arrive the window slides forward and more data can be sent. If the advertised window drops to zero the sender must wait.",
    ]),
    ("7 Congestion Control", [
        "Congestion control prevents senders from overwhelming the network itself rather than the receiver. TCP maintains a congestion window that limits the amount of unacknowledged data.",
        "Slow start doubles the congestion window every round trip time until a threshold is reached; congestion avoidance then increases it by one segment per round trip. A lost segment, detected by a timeout or by three duplicate acknowledgements, is treated as a sign of congestion and the window is reduced.",
    ]),
    ("8 DNS", [
        "The Domain Name System translates human friendly names such as www.example.com into IP addresses. It is a distributed, hierarchical database. Root servers know the top level domain servers, which know the authoritative servers for each domain.",
        "A resolver asks these servers in turn, or asks a recursive resolver that does the work on its behalf. Answers are cached for a time to live so that repeated lookups are fast. DNS normally uses UDP on port 53.",
    ]),
    ("9 HTTP and HTTPS", [
        "HTTP, the Hypertext Transfer Protocol, is the application protocol of the web. A client sends a request with a method such as GET or POST, a path and headers; the server returns a status code, headers and a body. HTTP runs on top of TCP, usually on port 80.",
        "HTTPS is HTTP carried over TLS. TLS provides encryption, integrity and server authentication using certificates. Before application data flows, the TLS handshake negotiates keys; this happens after the TCP three-way handshake.",
    ]),
]


def build(out: Path) -> None:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_title("Computer Networks - Revision Notes")
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 22)
    pdf.cell(0, 14, "Computer Networks", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 13)
    pdf.cell(0, 9, "Revision Notes", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 6, "These notes cover the fundamentals of computer networking: layered models, the TCP/IP suite, "
                         "the transport protocols TCP and UDP, connection establishment, flow and congestion control, "
                         "name resolution and the web protocols.")
    for title, paras in SECTIONS:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 12, title, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        pdf.set_font("Helvetica", "", 11)
        for p in paras:
            pdf.multi_cell(0, 6, p)
            pdf.ln(3)
    out.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(out))
    print(f"wrote {out}")


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "assets" / "demo_computer_networks.pdf")
