from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
from apscheduler.schedulers.background import BackgroundScheduler
import os
import PyPDF2
import re
import json
import requests
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf'}
APP_URL = os.getenv('APP_URL', 'https://tiffintreats.onrender.com')

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Initialize scheduler
scheduler = BackgroundScheduler()

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

def extract_structure_from_pdf(pdf_path):
    try:
        with open(pdf_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            total_pages = len(pdf_reader.pages)
            document_structure = {
                'title': '',
                'chapters': [],
                'pages': [],
                'metadata': {
                    'totalPages': total_pages,
                    'processedAt': datetime.now().isoformat()
                }
            }
            
            # Extract text from first page to find title
            first_page = pdf_reader.pages[0].extract_text()
            lines = first_page.split('\n')
            document_structure['title'] = lines[0].strip()
            
            current_chapter = None
            chapter_pattern = re.compile(r'^(?:Chapter|CHAPTER)\s+\d+|^\d+\.\s+')
            heading_pattern = re.compile(r'^[A-Z][^a-z]{2,}|^(?:Chapter|Section)\s+\d+|\d+\.\d+\s+[A-Z]')
            
            for page_num in range(total_pages):
                page = pdf_reader.pages[page_num]
                text = page.extract_text()
                lines = text.split('\n')
                page_content = []
                page_structure = {
                    'number': page_num + 1,
                    'content': '',
                    'headings': []
                }
                
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                        
                    if chapter_pattern.match(line):
                        current_chapter = {
                            'title': line,
                            'page': page_num + 1,
                            'content': []
                        }
                        document_structure['chapters'].append(current_chapter)
                    
                    elif heading_pattern.match(line):
                        page_structure['headings'].append({
                            'text': line,
                            'position': len(page_content)
                        })
                    
                    if current_chapter:
                        current_chapter['content'].append(line)
                    
                    page_content.append(line)
                
                page_structure['content'] = '\n'.join(page_content)
                document_structure['pages'].append(page_structure)
            
            return document_structure
    except Exception as e:
        logger.error(f"PDF processing error: {str(e)}")
        raise Exception("Failed to process PDF file")

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
                document_structure = extract_structure_from_pdf(filepath)
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
        'version': '1.1.0'
    }), 200

def initialize_scheduler():
    if not scheduler.running:
        scheduler.add_job(func=ping_server, trigger="interval", minutes=14)
        scheduler.start()
        logger.info("Scheduler started successfully")

if __name__ == '__main__':
    initialize_scheduler()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
