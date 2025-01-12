from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
from apscheduler.schedulers.background import BackgroundScheduler
import os
import PyPDF2
import re
import requests
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional
import fitz  # PyMuPDF
import nltk
from nltk.tokenize import sent_tokenize
import json
from dataclasses import dataclass, asdict
from collections import defaultdict

# Download required NLTK data
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@dataclass
class Heading:
    text: str
    level: int
    page_number: int
    position: int


@dataclass
class TableCell:
    text: str
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1

@dataclass
class Table:
    cells: List[TableCell]
    bbox: tuple[float, float, float, float]
    page_number: int
    row_count: int
    col_count: int

@dataclass
class Page:
    number: int
    content: str
    headings: List[Dict]
    paragraphs: List[str]
    tables: List[Dict]
    images: List[Dict]

@dataclass
class Chapter:
    title: str
    page_start: int
    page_end: Optional[int]
    content: List[str]
    subsections: List[Dict]

class PDFProcessor:
    def __init__(self):
        self.heading_patterns = {
            'chapter': re.compile(r'^(?:Chapter|CHAPTER)\s+\d+|^\d+\.\s+'),
            'section': re.compile(r'^\d+\.\d+\s+[A-Z]'),
            'subsection': re.compile(r'^\d+\.\d+\.\d+\s+[A-Z]'),
            'heading': re.compile(r'^[A-Z][^a-z]{2,}')
        }
        self.ignore_patterns = [
            re.compile(r'^Page \d+$'),
            re.compile(r'^\d+$'),
            re.compile(r'^Copyright'),
            re.compile(r'^All rights reserved'),
        ]
        self.table_markers = [
            re.compile(r'^Table\s+\d+'),
            re.compile(r'^\s*\|.*\|.*\|\s*$'),  # Markdown-style tables
            re.compile(r'^\s*[-+]+[-+|]+[-+]+\s*$')  # Table separators
        ]


    def is_heading(self, text: str) -> tuple[bool, int]:
        if self.heading_patterns['chapter'].match(text):
            return True, 1
        elif self.heading_patterns['section'].match(text):
            return True, 2
        elif self.heading_patterns['subsection'].match(text):
            return True, 3
        elif self.heading_patterns['heading'].match(text):
            return True, 4
        return False, 0

    def should_ignore(self, text: str) -> bool:
        return any(pattern.match(text) for pattern in self.ignore_patterns)

    def extract_tables(self, page: fitz.Page) -> List[Dict]:
        tables = []
        try:
            # Get page dimensions for relative positioning
            page_rect = page.rect
            
            # Get blocks that might contain tables
            blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_PRESERVE_WHITESPACE)
            
            current_table = None
            current_cells = []
            
            for block in blocks.get("blocks", []):
                if block.get("type") == 0:  # Text block
                    lines = block.get("lines", [])
                    
                    # Check if this block looks like a table
                    is_table_content = False
                    for line in lines:
                        spans = line.get("spans", [])
                        if spans:
                            text = "".join(span.get("text", "") for span in spans)
                            if any(pattern.match(text) for pattern in self.table_markers):
                                is_table_content = True
                                break
                    
                    if is_table_content:
                        # Process as table content
                        if not current_table:
                            current_table = {
                                "bbox": block["bbox"],
                                "rows": [],
                                "col_count": 0
                            }
                        
                        # Extract cells from lines
                        for line in lines:
                            row = []
                            for span in line.get("spans", []):
                                text = span.get("text", "").strip()
                                if text:
                                    cell = {
                                        "text": text,
                                        "bbox": span["bbox"],
                                        "font": span.get("font", ""),
                                        "size": span.get("size", 0)
                                    }
                                    row.append(cell)
                            
                            if row:
                                current_table["rows"].append(row)
                                current_table["col_count"] = max(
                                    current_table["col_count"],
                                    len(row)
                                )
                    
                    elif current_table:
                        # End of table detected
                        if current_table["rows"]:
                            tables.append(self._normalize_table(current_table))
                        current_table = None
            
            # Add any remaining table
            if current_table and current_table["rows"]:
                tables.append(self._normalize_table(current_table))
            
            return tables
        
        except Exception as e:
            logger.warning(f"Table extraction error: {str(e)}")
            return []

    def _normalize_table(self, table_data: Dict) -> Dict:
        """Normalizes table data to ensure consistent structure."""
        normalized = {
            "bbox": table_data["bbox"],
            "cells": [],
            "row_count": len(table_data["rows"]),
            "col_count": table_data["col_count"]
        }
        
        # Normalize cells into a regular grid
        for row_idx, row in enumerate(table_data["rows"]):
            for col_idx, cell in enumerate(row):
                normalized["cells"].append({
                    "text": cell["text"],
                    "row": row_idx,
                    "col": col_idx,
                    "bbox": cell["bbox"],
                    "font": cell.get("font", ""),
                    "size": cell.get("size", 0)
                })
        
        return normalized
    def extract_images(self, page: fitz.Page) -> List[Dict]:
        images = []
        try:
            image_list = page.get_images(full=True)
            for img_index, img in enumerate(image_list):
                xref = img[0]
                base_image = page.parent.extract_image(xref)
                if base_image:
                    images.append({
                        'index': img_index,
                        'bbox': img[1],
                        'size': (base_image["width"], base_image["height"]),
                        'type': base_image["ext"]
                    })
        except Exception as e:
            logger.warning(f"Image extraction error: {str(e)}")
        return images

    def process_pdf(self, file_path: str) -> Dict:
        doc = None
        try:
            doc = fitz.open(file_path)
            document_structure = {
                'title': '',
                'chapters': [],
                'pages': [],
                'metadata': {
                    'totalPages': len(doc),
                    'processedAt': datetime.now().isoformat(),
                    'author': doc.metadata.get('author', ''),
                    'creator': doc.metadata.get('creator', ''),
                    'producer': doc.metadata.get('producer', ''),
                    'subject': doc.metadata.get('subject', ''),
                    'keywords': doc.metadata.get('keywords', '')
                }
            }

            if len(doc) > 0:
                first_page = doc[0]
                text_blocks = first_page.get_text("blocks")
                if text_blocks:
                    document_structure['title'] = text_blocks[0][4].strip()

            current_chapter = None
            toc = doc.get_toc()
            chapter_page_ranges = self._get_chapter_ranges(toc, len(doc))
            processed_pages = []

            with ThreadPoolExecutor() as executor:
                future_to_page = {
                    executor.submit(self._process_page, doc[page_num], page_num): page_num
                    for page_num in range(len(doc))
                }

                for future in as_completed(future_to_page):
                    page_num = future_to_page[future]
                    try:
                        page_structure = future.result()
                        processed_pages.append(page_structure)
                        
                        chapter_info = next(
                            (ch for ch in chapter_page_ranges if ch['start'] <= page_num <= ch.get('end', len(doc)-1)),
                            None
                        )
                        
                        if chapter_info and chapter_info['start'] == page_num:
                            current_chapter = Chapter(
                                title=chapter_info['title'],
                                page_start=page_num + 1,
                                page_end=chapter_info.get('end'),
                                content=[],
                                subsections=[]
                            )
                            document_structure['chapters'].append(asdict(current_chapter))
                        
                        if current_chapter:
                            current_chapter.content.extend(page_structure.paragraphs)
                            
                            for heading in page_structure.headings:
                                if heading['level'] > 1:
                                    current_chapter.subsections.append({
                                        'title': heading['text'],
                                        'page': page_num + 1,
                                        'level': heading['level']
                                    })
                    except Exception as e:
                        logger.error(f"Error processing page {page_num}: {str(e)}")

            processed_pages.sort(key=lambda x: x.number)
            document_structure['pages'] = [asdict(page) for page in processed_pages]
            return document_structure

        except Exception as e:
            logger.error(f"PDF processing error: {str(e)}")
            raise Exception(f"Failed to process PDF file: {str(e)}")

        finally:
            if doc:
                doc.close()

    def _get_chapter_ranges(self, toc: List, total_pages: int) -> List[Dict]:
        chapter_ranges = []
        for i, (level, title, page) in enumerate(toc):
            if level == 1:
                chapter = {
                    'title': title,
                    'start': page - 1,
                    'end': toc[i+1][2] - 2 if i < len(toc) - 1 else total_pages - 1
                }
                chapter_ranges.append(chapter)
        return chapter_ranges

    def _process_page(self, page: fitz.Page, page_num: int) -> Page:
        text = page.get_text("text")
        lines = text.split('\n')
        
        paragraphs = []
        headings = []
        current_paragraph = []
        
        for line_num, line in enumerate(lines):
            line = line.strip()
            if not line or self.should_ignore(line):
                continue

            is_heading, level = self.is_heading(line)
            if is_heading:
                if current_paragraph:
                    paragraphs.append(' '.join(current_paragraph))
                    current_paragraph = []
                
                headings.append({
                    'text': line,
                    'level': level,
                    'position': len(paragraphs)
                })
            else:
                current_paragraph.append(line)
                
                if line.endswith('.') or line.endswith('?') or line.endswith('!'):
                    paragraphs.append(' '.join(current_paragraph))
                    current_paragraph = []

        if current_paragraph:
            paragraphs.append(' '.join(current_paragraph))

        return Page(
            number=page_num + 1,
            content=text,
            headings=headings,
            paragraphs=paragraphs,
            tables=self.extract_tables(page),
            images=self.extract_images(page)
        )

# Flask application setup
app = Flask(__name__)
CORS(app)

UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf'}
APP_URL = os.getenv('APP_URL', 'https://tiffintreats.onrender.com')

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024  # 32MB max file size

scheduler = BackgroundScheduler()
pdf_processor = PDFProcessor()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def ping_server():
    try:
        response = requests.get(f"{APP_URL}/health")
        if response.status_code == 200:
            logger.info(f"Self-ping successful at {datetime.now()}")
        else:
            logger.warning(f"Self-ping failed with status code {response.status_code}")
    except Exception as e:
        logger.error(f"Self-ping error: {str(e)}")

@app.route('/upload', methods=['POST'])
def upload_file():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file part'}), 400
            
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No selected file'}), 400
            
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            try:
                document_structure = pdf_processor.process_pdf(filepath)
                return jsonify(document_structure)
            except Exception as e:
                return jsonify({'error': str(e)}), 500
            finally:
                if os.path.exists(filepath):
                    os.remove(filepath)
        
        return jsonify({'error': 'Invalid file type'}), 400
    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'version': '1.2.0'
    }), 200

def initialize_scheduler():
    if not scheduler.running:
        scheduler.add_job(func=ping_server, trigger="interval", minutes=14)
        scheduler.start()
        logger.info("Scheduler started successfully")

if __name__ == '__main__':
    initialize_scheduler()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
