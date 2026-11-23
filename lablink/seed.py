"""Demo data: a realistic campus with labs in five departments.

Dates are generated relative to "now" so the demo always shows a live conflict
on the Mechanical department's 3D printer.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .models import (
    AccessRequest,
    Booking,
    BookingStatus,
    Equipment,
    EquipmentNeed,
    ProjectType,
    RequestStatus,
    RiskLevel,
    Role,
    User,
)
from .repository import Repository
from .scheduling import OPEN_WEEKDAYS

USERS = [
    User("s_ananya", "Ananya Sharma", Role.STUDENT, "Computer Science", "ananya@lablink.example.edu", ('3D Printer',)),
    User("s_rahul", "Rahul Nair", Role.STUDENT, "Mechanical", "rahul@lablink.example.edu", ('3D Printer', 'Laser Cutter')),
    User("s_meera", "Meera Pillai", Role.STUDENT, "Electronics", "meera@lablink.example.edu", ('3D Printer', 'Soldering Station')),
    User("s_kiran", "Kiran Gowda", Role.STUDENT, "Mechanical", "kiran@lablink.example.edu", ('3D Printer',)),
    User("s_arjun", "Arjun Das", Role.STUDENT, "Biotechnology", "arjun@lablink.example.edu", ()),
    User("s_neha", "Neha Kulkarni", Role.STUDENT, "Design", "neha@lablink.example.edu", ('3D Printer',)),
    User("f_rao", "Prof. Lakshmi Rao", Role.FACULTY, "Computer Science", "rao@lablink.example.edu"),
    User("f_iyer", "Prof. Suresh Iyer", Role.FACULTY, "Mechanical", "iyer@lablink.example.edu"),
    User("f_khan", "Dr. Farah Khan", Role.FACULTY, "Electronics", "khan@lablink.example.edu"),
    User("a_lab", "Lab Coordinator", Role.ADMIN, "Central Facilities", "lab@lablink.example.edu"),
]

EQUIPMENT = [
    Equipment("eq_fdm_printer", "Ultimaker S5 3D Printer", "3D Printer", "Mechanical", "Fabrication Lab (M-104)", 1,
              "Large-format FDM printer for PLA/ABS/PETG parts up to 330 x 240 x 300 mm. Ideal for enclosures, frames and mechanical prototypes.",
              ("fdm", "pla", "abs", "prototyping", "enclosure", "frame", "chassis"), True, RiskLevel.MODERATE, requires_approval=True),
    Equipment("eq_resin_printer", "Formlabs Form 3 Resin Printer", "3D Printer", "Design", "Design Studio (D-201)", 1,
              "High-detail SLA resin printer for small precise parts, moulds and biomedical models.",
              ("sla", "resin", "high detail", "prototyping", "prosthetic", "mould"), True, RiskLevel.MODERATE, requires_approval=True),
    Equipment("eq_laser_cutter", "Epilog Laser Cutter 60W", "Laser Cutter", "Mechanical", "Fabrication Lab (M-104)", 1,
              "Cuts and engraves acrylic, plywood and cardboard sheets up to 600 x 300 mm.",
              ("acrylic", "plywood", "engraving", "sheet", "cutting"), True, RiskLevel.HIGH, requires_approval=True),
    Equipment("eq_dso", "Keysight 100 MHz Digital Oscilloscope", "Oscilloscope", "Electronics", "Circuits Lab (E-002)", 2,
              "4-channel 100 MHz digital storage oscilloscope for signal analysis and debugging.",
              ("signal", "waveform", "frequency", "debugging", "mhz", "measure", "probe"), False),
    Equipment("eq_logic", "Saleae Logic Pro 16 Analyzer", "Logic Analyzer", "Electronics", "Embedded Lab (E-105)", 3,
              "16-channel logic analyzer for decoding I2C, SPI, UART and CAN protocols.",
              ("i2c", "spi", "uart", "protocol", "digital", "debugging"), False),
    Equipment("eq_fpga", "Xilinx Artix-7 FPGA Kit", "FPGA Kit", "Electronics", "Embedded Lab (E-105)", 4,
              "FPGA development board with switches, LEDs, VGA and PMOD ports for digital design.",
              ("fpga", "verilog", "vhdl", "digital design", "hardware"), False),
    Equipment("eq_solder", "Hakko Soldering & Rework Station", "Soldering Station", "Electronics", "Circuits Lab (E-002)", 4,
              "Temperature-controlled soldering and hot-air rework for PCB assembly.",
              ("pcb", "soldering", "smd", "assembly", "repair"), True, RiskLevel.MODERATE),
    Equipment("eq_jetson", "NVIDIA Jetson Orin Nano Kit", "Edge AI Kit", "Computer Science", "AI Lab (C-301)", 3,
              "Edge AI developer kit with camera module for on-device computer vision and robotics.",
              ("ai", "computer vision", "camera", "robotics", "edge", "deep learning", "drone"), False),
    Equipment("eq_gpu", "RTX 4090 Deep Learning Workstation", "GPU Workstation", "Computer Science", "AI Lab (C-301)", 2,
              "Workstation with 24 GB GPU for training machine-learning models.",
              ("gpu", "training", "deep learning", "machine learning", "neural network", "cuda", "classifier", "image"), False, requires_approval=True),
    Equipment("eq_esp32", "ESP32 IoT Sensor Kit", "Microcontroller Kit", "Computer Science", "IoT Lab (C-110)", 6,
              "ESP32 boards with soil moisture, temperature, humidity and gas sensors for IoT projects.",
              ("iot", "sensor", "wifi", "microcontroller", "soil", "irrigation", "arduino"), False),
    Equipment("eq_uvvis", "UV-Vis Spectrophotometer", "Spectrometer", "Biotechnology", "Bio-Analytics Lab (B-210)", 1,
              "Measures absorbance from 190–1100 nm for concentration and water-quality analysis.",
              ("absorbance", "spectroscopy", "water quality", "water samples", "nitrate", "concentration", "chemistry", "spectrophotometer"), True, RiskLevel.LOW, requires_approval=True),
    Equipment("eq_pcr", "Bio-Rad PCR Thermal Cycler", "PCR Machine", "Biotechnology", "Molecular Lab (B-115)", 1,
              "96-well thermal cycler for DNA amplification.",
              ("dna", "pcr", "genetics", "amplify", "amplification", "biology"), True, RiskLevel.MODERATE, requires_approval=True),
]


def open_day(today: date, n: int) -> date:
    """The n-th lab-open day after `today` (n=1 is the next open day)."""
    day = today
    count = 0
    while count < n:
        day += timedelta(days=1)
        if day.weekday() in OPEN_WEEKDAYS:
            count += 1
    return day


def past_open_day(today: date, n: int) -> date:
    day = today
    count = 0
    while count < n:
        day -= timedelta(days=1)
        if day.weekday() in OPEN_WEEKDAYS:
            count += 1
    return day


def _at(day: date, hour: int) -> datetime:
    return datetime.combine(day, time(hour))


def build_demo_data(now: datetime) -> tuple[list[User], list[Equipment], list[AccessRequest], list[Booking]]:
    today = now.date()
    created = now - timedelta(days=4)

    def req(rid, student, guide, title, desc, deadline, needs, status, kind, days_ago=4):
        return AccessRequest(
            id=rid, student_id=student, guide_id=guide, project_title=title, description=desc,
            deadline=deadline, needs=[EquipmentNeed(e, h) for e, h in needs],
            created_at=now - timedelta(days=days_ago), status=status, project_type=kind,
        )

    requests = [
        req("rq_drone", "s_ananya", "f_rao", "Autonomous inspection drone",
            "Quadcopter that inspects campus rooftops using onboard computer vision. Needs a printed carbon-PLA frame and a Jetson for vision.",
            open_day(today, 3), [("eq_fdm_printer", 6), ("eq_jetson", 8)], RequestStatus.APPROVED, ProjectType.CAPSTONE, days_ago=6),
        req("rq_arm", "s_rahul", "f_iyer", "Pick-and-place robot arm",
            "Four-axis robot arm for sorting lab samples; printed joints and laser-cut acrylic base.",
            today + timedelta(days=21), [("eq_fdm_printer", 15), ("eq_laser_cutter", 8)], RequestStatus.APPROVED, ProjectType.COURSEWORK, days_ago=5),
        req("rq_hand", "s_meera", "f_khan", "Low-cost myoelectric prosthetic hand",
            "EMG-controlled prosthetic hand for a rural health NGO. Needs printed finger linkages and oscilloscope for EMG signal tuning.",
            open_day(today, 5), [("eq_fdm_printer", 4), ("eq_dso", 3)], RequestStatus.APPROVED, ProjectType.RESEARCH, days_ago=3),
        req("rq_gearbox", "s_kiran", "f_iyer", "Planetary gearbox for EV kart",
            "Test-fit gearbox housing for the college EV go-kart team before machining in aluminium.",
            open_day(today, 2), [("eq_fdm_printer", 5)], RequestStatus.APPROVED, ProjectType.CLUB, days_ago=2),
        req("rq_biosensor", "s_arjun", "f_khan", "Low-cost water quality biosensor",
            "Measure nitrate levels in lake water samples and log them online with an IoT sensor node.",
            today + timedelta(days=18), [("eq_uvvis", 4), ("eq_esp32", 6)], RequestStatus.PENDING, ProjectType.RESEARCH, days_ago=1),
        req("rq_crop", "s_ananya", "f_rao", "Crop disease detection on the edge",
            "Train a leaf-disease classifier and deploy it on a camera-equipped edge device for farmers.",
            today + timedelta(days=30), [("eq_gpu", 10), ("eq_jetson", 6)], RequestStatus.PENDING, ProjectType.CAPSTONE, days_ago=0),
        req("rq_jewel", "s_neha", "f_iyer", "Parametric jewellery collection",
            "Course assignment: print a set of generative-design pendants in castable resin.",
            today + timedelta(days=25), [("eq_resin_printer", 18)], RequestStatus.APPROVED, ProjectType.COURSEWORK, days_ago=7),
    ]

    bookings: list[Booking] = []

    def book(bid, eq, user, rq, day, start_h, end_h, status=BookingStatus.CONFIRMED, made=created):
        bookings.append(Booking(bid, eq, user, rq, _at(day, start_h), _at(day, end_h), made, status))

    d1, d2, d3 = open_day(today, 1), open_day(today, 2), open_day(today, 3)
    # One coursework team booked the FDM printer early for days (slot squatting);
    # the resin printer is also busy, so urgent teams have nowhere to go.
    book("bk_p1", "eq_fdm_printer", "s_rahul", "rq_arm", d1, 9, 18)
    book("bk_p2", "eq_fdm_printer", "s_rahul", "rq_arm", d2, 9, 18)
    book("bk_p3", "eq_fdm_printer", "s_rahul", "rq_arm", d3, 9, 14)
    book("bk_r1", "eq_resin_printer", "s_neha", "rq_jewel", d1, 9, 18)
    book("bk_r2", "eq_resin_printer", "s_neha", "rq_jewel", d2, 9, 18)
    if today.weekday() in OPEN_WEEKDAYS:  # keep today's remaining hours busy too
        book("bk_p0", "eq_fdm_printer", "s_rahul", "rq_arm", today, 9, 18)
        book("bk_r0", "eq_resin_printer", "s_neha", "rq_jewel", today, 9, 18)
    # Four teams all asked for the printer at 10:00 on the next open day -> conflict.
    book("bk_w1", "eq_fdm_printer", "s_ananya", "rq_drone", d1, 10, 16, BookingStatus.WAITLISTED, now - timedelta(days=2))
    book("bk_w2", "eq_fdm_printer", "s_rahul", "rq_arm", d1, 10, 16, BookingStatus.WAITLISTED, now - timedelta(days=3))
    book("bk_w3", "eq_fdm_printer", "s_meera", "rq_hand", d1, 10, 14, BookingStatus.WAITLISTED, now - timedelta(days=1))
    book("bk_w4", "eq_fdm_printer", "s_kiran", "rq_gearbox", d1, 10, 15, BookingStatus.WAITLISTED, now - timedelta(hours=6))

    # Past usage feeds both the fairness score and the utilisation dashboard.
    for i, (eq, user, rq, s, e) in enumerate([
        ("eq_laser_cutter", "s_rahul", "rq_arm", 9, 13),
        ("eq_laser_cutter", "s_rahul", "rq_arm", 13, 17),
        ("eq_fdm_printer", "s_rahul", "rq_arm", 9, 13),
        ("eq_dso", "s_meera", "rq_hand", 14, 16),
        ("eq_jetson", "s_ananya", "rq_drone", 10, 12),
        ("eq_fdm_printer", "s_kiran", "rq_gearbox", 9, 17),
        ("eq_gpu", "s_ananya", "rq_drone", 9, 17),
        ("eq_esp32", "s_arjun", "rq_biosensor", 11, 13),
    ]):
        book(f"bk_past{i}", eq, user, rq, past_open_day(today, 1 + i % 5), s, e)

    return USERS, EQUIPMENT, requests, bookings


def seed_if_empty(repo: Repository, now: datetime) -> bool:
    if not repo.is_empty():
        return False
    repo.load(*build_demo_data(now))
    return True
