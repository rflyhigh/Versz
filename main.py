from flask import Flask, request, jsonify
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os
import pytz
import pymongo
from pymongo import MongoClient
from bson import ObjectId
import threading
import time
import requests
import bcrypt
import json

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
app.config['APP_URL'] = os.getenv('APP_URL')

# MongoDB setup
client = MongoClient(os.getenv('MONGODB_URI'))
db = client.tiffin_treats

# Login manager setup
login_manager = LoginManager()
login_manager.init_app(app)

class User(UserMixin):
    def __init__(self, user_data):
        self.user_data = user_data
        
    def get_id(self):
        return str(self.user_data['_id'])
    
    @property
    def is_admin(self):
        return self.user_data.get('is_admin', False)

@login_manager.user_loader
def load_user(user_id):
    user_data = db.users.find_one({'_id': ObjectId(user_id)})
    return User(user_data) if user_data else None

# Self-ping mechanism
def keep_alive():
    while True:
        try:
            response = requests.get(app.config['APP_URL'])
            print(f"Self-ping status: {response.status_code}")
        except Exception as e:
            print(f"Self-ping failed: {e}")
        time.sleep(600)  # Ping every 10 minutes

# Start the keep-alive thread
if app.config['APP_URL']:
    ping_thread = threading.Thread(target=keep_alive, daemon=True)
    ping_thread.start()

# Helper functions
def serialize_doc(doc):
    doc['_id'] = str(doc['_id'])
    return doc

# Authentication Routes
@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    user_data = db.users.find_one({'username': data['username']})
    
    if user_data and bcrypt.checkpw(data['password'].encode('utf-8'), 
                                   user_data['password'].encode('utf-8')):
        user = User(user_data)
        login_user(user)
        return jsonify({
            'message': 'Logged in successfully',
            'is_admin': user.is_admin
        })
    return jsonify({'error': 'Invalid credentials'}), 401

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return jsonify({'message': 'Logged out successfully'})

# Admin Routes
@app.route('/admin/users', methods=['POST'])
@login_required
def create_user():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    hashed = bcrypt.hashpw(data['password'].encode('utf-8'), bcrypt.gensalt())
    
    new_user = {
        'username': data['username'],
        'password': hashed.decode('utf-8'),
        'is_admin': data.get('is_admin', False),
        'created_at': datetime.utcnow()
    }
    
    result = db.users.insert_one(new_user)
    return jsonify({'message': 'User created successfully', 
                   'id': str(result.inserted_id)})

@app.route('/admin/tiffin', methods=['POST'])
@login_required
def create_tiffin():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    new_tiffin = {
        'date': datetime.strptime(data['date'], '%Y-%m-%d').date(),
        'time_slot': data['time_slot'],
        'menu': data['menu'],
        'price': float(data['price']),
        'status': 'cooking',
        'created_at': datetime.utcnow()
    }
    
    result = db.tiffins.insert_one(new_tiffin)
    return jsonify({'message': 'Tiffin created successfully', 
                   'id': str(result.inserted_id)})

@app.route('/admin/tiffin/<tiffin_id>/status', methods=['PUT'])
@login_required
def update_tiffin_status(tiffin_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    result = db.tiffins.update_one(
        {'_id': ObjectId(tiffin_id)},
        {'$set': {'status': data['status']}}
    )
    
    if result.modified_count:
        return jsonify({'message': 'Status updated successfully'})
    return jsonify({'error': 'Tiffin not found'}), 404

# User Routes
@app.route('/tiffins/today')
@login_required
def get_today_tiffins():
    ist = pytz.timezone('Asia/Kolkata')
    today = datetime.now(ist).date()
    
    tiffins = list(db.tiffins.find({
        'date': today
    }))
    
    return jsonify([serialize_doc(t) for t in tiffins])

@app.route('/order/<tiffin_id>/cancel', methods=['POST'])
@login_required
def cancel_order(tiffin_id):
    tiffin = db.tiffins.find_one({'_id': ObjectId(tiffin_id)})
    if not tiffin:
        return jsonify({'error': 'Tiffin not found'}), 404
    
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.now(ist)
    
    delivery_time = datetime.combine(
        tiffin['date'],
        datetime.strptime('09:00' if tiffin['time_slot'] == 'morning' else '21:00', 
                         '%H:%M').time()
    )
    deadline = delivery_time - timedelta(hours=3)
    
    if now > deadline:
        return jsonify({'error': 'Cancellation deadline passed'}), 400
    
    order = db.orders.find_one({
        'user_id': ObjectId(current_user.get_id()),
        'tiffin_id': ObjectId(tiffin_id)
    })
    
    if order:
        result = db.orders.update_one(
            {'_id': order['_id']},
            {
                '$set': {
                    'status': 'cancelled',
                    'cancellation_time': now
                }
            }
        )
        if result.modified_count:
            return jsonify({'message': 'Order cancelled successfully'})
    
    return jsonify({'error': 'Order not found'}), 404

# Poll Routes
@app.route('/admin/polls', methods=['POST'])
@login_required
def create_poll():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    new_poll = {
        'title': data['title'],
        'options': data['options'],
        'end_time': datetime.strptime(data['end_time'], '%Y-%m-%d %H:%M'),
        'created_at': datetime.utcnow()
    }
    
    result = db.polls.insert_one(new_poll)
    return jsonify({'message': 'Poll created successfully', 
                   'id': str(result.inserted_id)})

@app.route('/polls/active')
@login_required
def get_active_polls():
    now = datetime.utcnow()
    polls = list(db.polls.find({
        'end_time': {'$gt': now}
    }))
    
    return jsonify([serialize_doc(p) for p in polls])

@app.route('/polls/<poll_id>/vote', methods=['POST'])
@login_required
def vote_poll(poll_id):
    data = request.get_json()
    
    vote = {
        'poll_id': ObjectId(poll_id),
        'user_id': ObjectId(current_user.get_id()),
        'option': data['option'],
        'created_at': datetime.utcnow()
    }
    
    # Remove previous vote if exists
    db.poll_votes.delete_many({
        'poll_id': ObjectId(poll_id),
        'user_id': ObjectId(current_user.get_id())
    })
    
    result = db.poll_votes.insert_one(vote)
    return jsonify({'message': 'Vote recorded successfully'})

# Notice Routes
@app.route('/admin/notices', methods=['POST'])
@login_required
def create_notice():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    new_notice = {
        'title': data['title'],
        'content': data['content'],
        'created_at': datetime.utcnow()
    }
    
    result = db.notices.insert_one(new_notice)
    return jsonify({'message': 'Notice created successfully', 
                   'id': str(result.inserted_id)})

@app.route('/notices')
@login_required
def get_notices():
    notices = list(db.notices.find().sort('created_at', -1).limit(10))
    return jsonify([serialize_doc(n) for n in notices])

# Error Handlers
@app.errorhandler(404)
def not_found_error(error):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    app.run(debug=True)
