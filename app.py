from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from groq import Groq
import os
import time
import uuid
import json
import pickle
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'canvas-quiz-bot-secret-key')
socketio = SocketIO(app, cors_allowed_origins="*")

# Store active sessions
active_sessions = {}

# Configuration management (inspired by bingfarm structure)
class Config:
    def __init__(self):
        self.config_file = os.path.join('/app', 'config.json')
        self.sessions_file = os.path.join('/app', 'sessions.json')
        self.cookies_dir = os.path.join('/app', 'cookies')
        self.data = self.load_config()
        os.makedirs(self.cookies_dir, mode=0o700, exist_ok=True)
    
    def load_config(self):
        default = {
            'timeout': 60,
            'headless': False,  # False for visible browser in noVNC
            'window_size': '1920x1080'
        }
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file) as f:
                    return {**default, **json.load(f)}
            except:
                return default
        return default
    
    def save(self):
        with open(self.config_file, 'w') as f:
            json.dump(self.data, f, indent=2)
    
    def get_cookie_path(self, session_id):
        return os.path.join(self.cookies_dir, f'{session_id}.pkl')

config = Config()

class CanvasQuizBot:
    def __init__(self, groq_api_key, canvas_url):
        self.groq_client = Groq(api_key=groq_api_key)
        self.canvas_url = canvas_url
        self.driver = None
        self.session_id = str(uuid.uuid4())
        
    def initialize_browser(self):
        """Initialize Selenium WebDriver with Chrome - visible in noVNC"""
        chrome_options = Options()
        
        # Configure for X11 display (visible in noVNC)
        chrome_options.add_argument(f'--display={os.environ.get("DISPLAY", ":99")}')
        
        # Important: Do NOT use headless mode - we want to see it in noVNC!
        # chrome_options.add_argument('--headless')  # COMMENTED OUT
        
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_argument('--disable-web-security')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--start-maximized')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        # Use ChromeDriver
        service = Service('/usr/local/bin/chromedriver')
        
        self.driver = webdriver.Chrome(service=service, options=chrome_options)
        self.driver.implicitly_wait(10)
        
        # Send browser startup notification via SocketIO
        try:
            socketio.emit('browser_started', {
                'session_id': self.session_id,
                'message': 'Browser launched! Check noVNC viewer.'
            })
        except:
            pass
        
        return True
    
    def navigate_to_quiz(self):
        """Navigate to the Canvas quiz URL"""
        if not self.driver:
            raise Exception("Browser not initialized")
        
        self.driver.get(self.canvas_url)
        time.sleep(3)  # Wait for page load
        return True
    
    def extract_questions(self):
        """Extract all questions from the Canvas quiz page"""
        questions = []
        
        try:
            # Wait for quiz questions to load
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".question, .quiz_question"))
            )
            
            # Find all question containers
            question_elements = self.driver.find_elements(By.CSS_SELECTOR, ".question, .quiz_question, [class*='question']")
            
            for idx, question_el in enumerate(question_elements):
                try:
                    # Extract question text
                    question_text_el = question_el.find_element(By.CSS_SELECTOR, ".question_text, .text, [class*='question_text']")
                    question_text = question_text_el.text.strip()
                    
                    if not question_text:
                        continue
                    
                    question_data = {
                        'index': idx,
                        'text': question_text,
                        'type': 'unknown',
                        'options': [],
                        'element_id': question_el.get_attribute('id')
                    }
                    
                    # Detect question type and extract options
                    # Multiple choice (radio buttons)
                    radio_inputs = question_el.find_elements(By.CSS_SELECTOR, "input[type='radio']")
                    if radio_inputs:
                        question_data['type'] = 'multiple_choice'
                        for radio in radio_inputs:
                            label = self._find_label_for_input(radio)
                            question_data['options'].append({
                                'text': label,
                                'value': radio.get_attribute('value'),
                                'id': radio.get_attribute('id')
                            })
                    
                    # Multiple select (checkboxes)
                    checkbox_inputs = question_el.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")
                    if checkbox_inputs:
                        question_data['type'] = 'multiple_select'
                        for checkbox in checkbox_inputs:
                            label = self._find_label_for_input(checkbox)
                            question_data['options'].append({
                                'text': label,
                                'value': checkbox.get_attribute('value'),
                                'id': checkbox.get_attribute('id')
                            })
                    
                    # Essay question (textarea)
                    textarea = question_el.find_elements(By.CSS_SELECTOR, "textarea")
                    if textarea:
                        question_data['type'] = 'essay'
                        question_data['input_id'] = textarea[0].get_attribute('id')
                    
                    # Short answer (text input)
                    text_input = question_el.find_elements(By.CSS_SELECTOR, "input[type='text']")
                    if text_input and not radio_inputs and not checkbox_inputs:
                        question_data['type'] = 'short_answer'
                        question_data['input_id'] = text_input[0].get_attribute('id')
                    
                    questions.append(question_data)
                    
                except Exception as e:
                    print(f"Error extracting question {idx}: {str(e)}")
                    continue
            
            return questions
            
        except Exception as e:
            print(f"Error extracting questions: {str(e)}")
            return []
    
    def _find_label_for_input(self, input_element):
        """Find the label text for an input element"""
        try:
            # Try to find parent label
            parent = input_element.find_element(By.XPATH, "./..")
            if parent.tag_name.lower() == 'label':
                return parent.text.strip()
            
            # Try to find label by 'for' attribute
            input_id = input_element.get_attribute('id')
            if input_id:
                label = self.driver.find_element(By.CSS_SELECTOR, f"label[for='{input_id}']")
                return label.text.strip()
            
            # Try to find nearby text
            return parent.text.strip()
        except:
            return "Option"
    
    def get_answer_from_groq(self, question):
        """Get answer from Groq API"""
        try:
            prompt = self._build_prompt(question)
            
            completion = self.groq_client.chat.completions.create(
                messages=[
                    {
                        "role": "system",
                        "content": "You are a helpful assistant that provides accurate answers to quiz questions. Be concise and precise."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                model="llama-3.3-70b-versatile",
                temperature=0.3,
                max_tokens=1000 if question['type'] == 'essay' else 200
            )
            
            answer = completion.choices[0].message.content.strip()
            return answer
            
        except Exception as e:
            print(f"Groq API error: {str(e)}")
            raise e
    
    def _build_prompt(self, question):
        """Build appropriate prompt based on question type"""
        q_type = question['type']
        q_text = question['text']
        
        if q_type == 'multiple_choice':
            prompt = f"Answer this multiple choice question. Return ONLY the letter (A, B, C, D, etc.) of the correct answer, nothing else.\n\nQuestion: {q_text}\n\nOptions:\n"
            for idx, opt in enumerate(question['options']):
                prompt += f"{chr(65 + idx)}. {opt['text']}\n"
        
        elif q_type == 'multiple_select':
            prompt = f"Answer this multiple select question. Return ONLY the letters (e.g., 'A,C,D') of ALL correct answers separated by commas, nothing else.\n\nQuestion: {q_text}\n\nOptions:\n"
            for idx, opt in enumerate(question['options']):
                prompt += f"{chr(65 + idx)}. {opt['text']}\n"
        
        elif q_type == 'essay':
            prompt = f"Provide a comprehensive essay answer to this question:\n\n{q_text}\n\nWrite a detailed, well-structured response."
        
        elif q_type == 'short_answer':
            prompt = f"Provide a concise, direct answer to this question:\n\n{q_text}"
        
        else:
            prompt = f"Answer this question:\n\n{q_text}"
        
        return prompt
    
    def fill_answer(self, question, answer):
        """Fill in the answer on the page"""
        try:
            q_type = question['type']
            
            if q_type == 'multiple_choice':
                # Parse answer letter
                answer_letter = answer[0].upper() if answer else None
                if not answer_letter:
                    return False
                
                option_index = ord(answer_letter) - 65
                if 0 <= option_index < len(question['options']):
                    option = question['options'][option_index]
                    element = self.driver.find_element(By.ID, option['id'])
                    element.click()
                    return True
            
            elif q_type == 'multiple_select':
                # Parse answer letters
                answer_letters = [l.strip().upper() for l in answer.split(',')]
                for letter in answer_letters:
                    option_index = ord(letter) - 65
                    if 0 <= option_index < len(question['options']):
                        option = question['options'][option_index]
                        element = self.driver.find_element(By.ID, option['id'])
                        element.click()
                return True
            
            elif q_type in ['essay', 'short_answer']:
                element = self.driver.find_element(By.ID, question['input_id'])
                element.clear()
                element.send_keys(answer)
                return True
            
            return False
            
        except Exception as e:
            print(f"Error filling answer: {str(e)}")
            return False
    
    def solve_quiz(self, auto_submit=False):
        """Solve all questions in the quiz"""
        questions = self.extract_questions()
        results = []
        
        for question in questions:
            try:
                answer = self.get_answer_from_groq(question)
                filled = self.fill_answer(question, answer)
                
                results.append({
                    'question_index': question['index'],
                    'question_text': question['text'],
                    'question_type': question['type'],
                    'answer': answer,
                    'filled': filled
                })
                
                time.sleep(0.5)  # Small delay between questions
                
            except Exception as e:
                results.append({
                    'question_index': question['index'],
                    'question_text': question['text'],
                    'error': str(e)
                })
        
        # Auto-submit if requested
        if auto_submit:
            try:
                submit_button = self.driver.find_element(By.CSS_SELECTOR, "button[type='submit'], input[type='submit'], .submit_button")
                submit_button.click()
            except Exception as e:
                print(f"Auto-submit error: {str(e)}")
        
        return results
    
    def close(self):
        """Close the browser and save session"""
        if self.driver:
            # Save cookies before closing
            try:
                cookie_path = config.get_cookie_path(self.session_id)
                with open(cookie_path, 'wb') as f:
                    pickle.dump(self.driver.get_cookies(), f)
            except Exception as e:
                print(f"Could not save cookies: {e}")
            
            self.driver.quit()
            self.driver = None
    
    def restore_session(self):
        """Restore previous session from cookies"""
        cookie_path = config.get_cookie_path(self.session_id)
        if not os.path.exists(cookie_path):
            return False
        
        try:
            self.initialize_browser()
            self.driver.get(self.canvas_url)
            
            # Load cookies
            with open(cookie_path, 'rb') as f:
                cookies = pickle.load(f)
            
            for cookie in cookies:
                try:
                    self.driver.add_cookie(cookie)
                except Exception as e:
                    print(f"Could not add cookie: {e}")
            
            self.driver.refresh()
            return True
        except Exception as e:
            print(f"Session restore failed: {e}")
            return False


# Flask routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/novnc')
def novnc():
    """Serve the noVNC viewer page"""
    return render_template('novnc.html')

@app.route('/novnc-direct')
def novnc_direct():
    """Direct link to noVNC (for local development)"""
    import requests
    try:
        # Proxy to noVNC on port 6080
        resp = requests.get('http://localhost:6080/vnc.html', stream=True)
        return resp.content, resp.status_code, resp.headers.items()
    except:
        return "noVNC not available. Use port 6080 directly or check if service is running.", 503

@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'active_sessions': len(active_sessions),
        'novnc_available': True,
        'novnc_port': 6080
    })

# WebSocket events
@socketio.on('connect')
def handle_connect():
    emit('connected', {'data': 'Connected to Canvas Quiz Bot'})

@socketio.on('request_status')
def handle_status_request():
    emit('status_update', {
        'active_sessions': len(active_sessions),
        'novnc_url': f'http://localhost:6080/vnc.html'
    })

@app.route('/api/start-session', methods=['POST'])
def start_session():
    try:
        data = request.json
        api_key = data.get('apiKey')
        canvas_url = data.get('canvasUrl')
        
        if not api_key or not canvas_url:
            return jsonify({'error': 'API key and Canvas URL are required'}), 400
        
        # Create new bot instance
        bot = CanvasQuizBot(api_key, canvas_url)
        bot.initialize_browser()
        
        # Store session
        active_sessions[bot.session_id] = bot
        
        return jsonify({
            'sessionId': bot.session_id,
            'message': 'Session started successfully'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/navigate', methods=['POST'])
def navigate():
    try:
        data = request.json
        session_id = data.get('sessionId')
        
        if session_id not in active_sessions:
            return jsonify({'error': 'Session not found'}), 404
        
        bot = active_sessions[session_id]
        bot.navigate_to_quiz()
        
        return jsonify({'message': 'Navigated to Canvas page'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/extract-questions', methods=['POST'])
def extract_questions():
    try:
        data = request.json
        session_id = data.get('sessionId')
        
        if session_id not in active_sessions:
            return jsonify({'error': 'Session not found'}), 404
        
        bot = active_sessions[session_id]
        questions = bot.extract_questions()
        
        # Format questions for response
        formatted_questions = []
        for q in questions:
            formatted_questions.append({
                'index': q['index'],
                'text': q['text'],
                'type': q['type'],
                'options': [{'text': opt['text']} for opt in q.get('options', [])]
            })
        
        return jsonify({
            'questions': formatted_questions,
            'count': len(formatted_questions)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/solve-quiz', methods=['POST'])
def solve_quiz():
    try:
        data = request.json
        session_id = data.get('sessionId')
        auto_submit = data.get('autoSubmit', False)
        
        if session_id not in active_sessions:
            return jsonify({'error': 'Session not found'}), 404
        
        bot = active_sessions[session_id]
        results = bot.solve_quiz(auto_submit)
        
        answered_count = sum(1 for r in results if r.get('filled', False))
        
        return jsonify({
            'results': results,
            'totalQuestions': len(results),
            'answeredQuestions': answered_count
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/close-session', methods=['POST'])
def close_session():
    try:
        data = request.json
        session_id = data.get('sessionId')
        
        if session_id in active_sessions:
            bot = active_sessions[session_id]
            bot.close()
            del active_sessions[session_id]
        
        return jsonify({'message': 'Session closed'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
