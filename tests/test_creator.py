from nexlinq.creator import (
    CREATOR_DATA_BYTES_PER_REPORT,
    FRAME_SIZE,
    HID_REPORT_SIZE,
    MATRIX_HEIGHT,
    MATRIX_WIDTH,
    FrameBuffer,
    build_creator_frame_reports,
    build_creator_init_report,
    build_creator_upload_reports,
    build_creator_widget_report,
    resolve_percentage_widths,
)


def test_framebuffer_uses_row_major_rgb888() -> None:
    frame = FrameBuffer()
    frame.set_pixel(0, 0, (1, 2, 3))
    frame.set_pixel(MATRIX_WIDTH - 1, MATRIX_HEIGHT - 1, (4, 5, 6))

    payload = frame.to_bytes()

    assert len(payload) == FRAME_SIZE
    assert payload[:3] == bytes((1, 2, 3))
    assert payload[-3:] == bytes((4, 5, 6))


def test_fill_rect_changes_only_requested_region() -> None:
    frame = FrameBuffer()
    frame.fill_rect(2, 1, 3, 2, (10, 20, 30))

    payload = frame.to_bytes()

    def pixel(x: int, y: int) -> bytes:
        offset = (y * MATRIX_WIDTH + x) * 3
        return payload[offset : offset + 3]

    assert pixel(1, 1) == bytes((0, 0, 0))
    assert pixel(2, 1) == bytes((10, 20, 30))
    assert pixel(4, 2) == bytes((10, 20, 30))
    assert pixel(5, 2) == bytes((0, 0, 0))


def test_creator_init_report_matches_capture() -> None:
    report = build_creator_init_report()

    assert len(report) == HID_REPORT_SIZE
    assert report[:7] == bytes((0x01, 0x05, 0xFF, 0x00, 0x00, 0x01, 0x01))
    assert report[7:] == bytes(HID_REPORT_SIZE - 7)


def test_creator_frame_is_split_into_captured_chunk_format() -> None:
    frame = bytes(index % 256 for index in range(FRAME_SIZE))
    reports = build_creator_frame_reports(frame)

    assert len(reports) == 49
    assert all(len(report) == HID_REPORT_SIZE for report in reports)

    first = reports[0]
    assert first[:8] == bytes((0x01, 0x05, 0x00, 0x00, 0x00, 0x3A, 0x5A, 0x0A))
    assert first[8:64] == frame[:CREATOR_DATA_BYTES_PER_REPORT]

    last = reports[-1]
    assert last[:8] == bytes((0x01, 0x05, 0x00, 0x00, 0x30, 0x0E, 0x5A, 0x0A))
    assert last[8:20] == frame[-12:]
    assert last[20:] == bytes(44)


def test_creator_frame_reports_reassemble_without_loss() -> None:
    frame = bytes(index % 251 for index in range(FRAME_SIZE))
    reports = build_creator_frame_reports(frame)

    reconstructed = b"".join(
        report[8 : 8 + report[5] - 2]
        for report in reports
    )

    assert reconstructed == frame


def test_creator_widget_report_matches_capture() -> None:
    report = build_creator_widget_report()

    assert len(report) == HID_REPORT_SIZE
    assert report[:21] == bytes.fromhex(
        "01 03 01 00 00 22 01 04 01 01 02 "
        "ff ff ff 00 00 00 ff 00 64 00"
    )
    assert report[21:] == bytes(HID_REPORT_SIZE - 21)


def test_creator_upload_contains_init_frame_and_activation() -> None:
    reports = build_creator_upload_reports(bytes(FRAME_SIZE))

    assert len(reports) == 51
    assert reports[0][1:3] == bytes((0x05, 0xFF))
    assert reports[1][1:3] == bytes((0x05, 0x00))
    assert reports[-1][1] == 0x03


def test_percentage_widths_fill_exact_matrix_width() -> None:
    widths = resolve_percentage_widths((25, 50, 25))

    assert widths == (23, 45, 22)
    assert sum(widths) == MATRIX_WIDTH
