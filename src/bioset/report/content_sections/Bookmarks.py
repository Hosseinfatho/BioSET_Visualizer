import json
import json
import os
from pathlib import Path

from bioset.report.content_sections.PDFSection import PDFSection, SUBTITLE_FONT_SIZE, TITLE_COLOR, FONT_SIZE, COLOR, \
    BACKGROUND_COLOR


class ChannelContent:
    def __init__(self, name: str, color: str, range_values: list[float]):
        self.name = name
        self.color = color
        self.range = range_values


class BookmarkContent:
    def __init__(self, title: str, category: str, created: str, description: str, channels: list[ChannelContent],
                 notes: str, thumbnail_path: str = None):
        self.title = title
        self.category = category
        self.created = created
        self.description = description
        self.channels = channels
        self.notes = notes
        self.thumbnail_path = thumbnail_path


class Bookmarks(PDFSection):
    def __init__(self, bookmarks: list[BookmarkContent]):
        super().__init__("Bookmarks")
        self.bookmarks = bookmarks

    def append_pdf_bytes(self, pdf):
        if not self.bookmarks:
            return pdf

        pdf.set_font('Arial', 'B', SUBTITLE_FONT_SIZE)
        pdf.set_text_color(TITLE_COLOR)
        pdf.cell(0, 10, self.get_title(), ln=1, align='L')
        pdf.ln(2)

        for bookmark in self.bookmarks:
            has_thumbnail = bookmark.thumbnail_path is not None
            effective_width = pdf.w - pdf.l_margin - pdf.r_margin
            padding = 5

            text_width = 130 if has_thumbnail else effective_width - (2 * padding)
            image_width = 60
            image_x = pdf.l_margin + effective_width - padding - image_width

            estimated_height = 0

            def get_field_h(label, value):
                label_str = f"{label}: "
                pdf.set_font('Arial', 'B', FONT_SIZE)
                label_w = pdf.get_string_width(label_str)
                pdf.set_font('Arial', '', FONT_SIZE)
                lines = pdf.multi_cell(text_width - label_w, 5, str(value), split_only=True)
                return len(lines) * 5

            estimated_height += get_field_h("Title", bookmark.title)
            estimated_height += get_field_h("Category", bookmark.category)
            estimated_height += get_field_h("Created", bookmark.created)
            estimated_height += get_field_h("Description", bookmark.description)

            if bookmark.channels:
                estimated_height += 6
                for channel in bookmark.channels:
                    pdf.set_font('Arial', '', FONT_SIZE)
                    lines = pdf.multi_cell(text_width - 5, 5,
                                           f"{channel.name} (Range: {channel.range[0]}-{channel.range[1]})",
                                           split_only=True)
                    estimated_height += len(lines) * 5

            total_h = estimated_height + (2 * padding)

            # Check if the bookmark fits on the current page, if not, add a page break
            page_bottom = pdf.h - pdf.b_margin
            if pdf.get_y() + total_h > page_bottom:
                pdf.add_page()

            start_y = pdf.get_y()
            pdf.set_fill_color(BACKGROUND_COLOR)
            pdf.rect(pdf.l_margin, start_y, effective_width, total_h, style='F', round_corners=True, corner_radius=3)

            pdf.set_y(start_y + padding)

            def add_field(label, value):
                pdf.set_x(pdf.l_margin + padding)
                pdf.set_font('Arial', 'B', FONT_SIZE)
                pdf.set_text_color(TITLE_COLOR)
                label_str = f"{label}: "
                pdf.write(5, label_str)

                pdf.set_font('Arial', '', FONT_SIZE)
                pdf.set_text_color(COLOR)
                label_w = pdf.get_string_width(label_str)
                pdf.multi_cell(text_width - label_w, 5, str(value), align='L')
                pdf.set_x(pdf.l_margin + padding)

            add_field("Title", bookmark.title)
            add_field("Category", bookmark.category)
            add_field("Created", bookmark.created)
            add_field("Description", bookmark.description)

            if bookmark.channels:
                pdf.set_x(pdf.l_margin + padding)
                pdf.set_font('Arial', 'B', FONT_SIZE)
                pdf.set_text_color(TITLE_COLOR)
                pdf.write(5, f"Channels:")
                pdf.ln(6)
                for channel in bookmark.channels:
                    y = pdf.get_y()
                    pdf.set_fill_color(channel.color)
                    pdf.circle(pdf.l_margin + padding + 2, y + 2.5, radius=1.5, style='F')

                    pdf.set_x(pdf.l_margin + padding + 5)
                    pdf.set_font('Arial', '', FONT_SIZE)
                    pdf.set_text_color(COLOR)
                    pdf.multi_cell(text_width - 5, 5, f"{channel.name} (Range: {channel.range[0]}-{channel.range[1]})")
                    pdf.set_x(pdf.l_margin + padding)

            if has_thumbnail:
                pdf.image(bookmark.thumbnail_path, x=image_x, y=start_y + padding, w=image_width)

            pdf.set_y(start_y + total_h + 5)

        return pdf


def load_all_bookmarks(base_path: str) -> list[BookmarkContent]:
    bookmarks = []

    # Recordings are per-dataset now, so this folder simply does not exist for
    # a dataset nobody has bookmarked yet. That is an empty report section, not
    # an error that should sink the whole export.
    if not os.path.isdir(base_path):
        print(f"[report] no bookmarks for this dataset ({base_path})")
        return bookmarks

    with os.scandir(base_path) as dir_content:
        for el in dir_content:
            if not el.is_dir():
                continue

            category_path = f'{base_path}/{el.name}'

            for root, _, files in os.walk(category_path):
                for file in files:
                    if not file.endswith(".json"):
                        continue

                    file_path = Path(root) / file
                    try:
                        file_path_obj = Path(file).stem
                        with open(file_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)

                            channels = []

                            for channel in data.get("channels"):
                                channels.append(ChannelContent(
                                    name=channel.get("name"),
                                    color=channel.get("color"),
                                    range_values=channel.get("range")
                                ))

                            thumbnail_path = f'{base_path}/{el.name}/{file_path_obj}.png'
                            if not Path(thumbnail_path).is_file():
                                thumbnail_path = None

                            bookmarks.append(BookmarkContent(
                                title=data.get("title"),
                                category=data.get("category"),
                                created=data.get("created"),
                                description=data.get("description"),
                                channels=channels,
                                notes=data.get("notes"),
                                thumbnail_path=thumbnail_path
                            ))

                    except Exception as e:
                        print(f"Error loading bookmark {file_path}: {e}")

    bookmarks.sort(key=lambda x: x.created, reverse=True)
    return bookmarks
