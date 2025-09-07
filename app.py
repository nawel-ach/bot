
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from datetime import datetime
import uuid
import re
import requests
import json
from thefuzz import process, fuzz
from dotenv import load_dotenv


load_dotenv()

app = Flask(__name__, template_folder='templates', static_folder='static')
CORS(app)

# Database configuration
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'database': os.getenv('DB_NAME', 'product_db'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', 'your_password'),
    'port': os.getenv('DB_PORT', '5432')
}

# DeepSeek API configuration
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY', 'your_deepseek_api_key')
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

class ChatSession:
    def __init__(self):
        self.sessions = {}
    
    def get_session(self, session_id):
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                'id': session_id,
                'state': 'welcome',
                'conversation_id': str(uuid.uuid4()),
                'brand': None,
                'model': None,
                'year': None,
                'spare_part': None,
                'reference': None,
                'search_type': None,
                'temp_data': {},
                'awaiting_confirmation': None,
                'messages': []
            }
        return self.sessions[session_id]
    
    def update_session(self, session_id, data):
        session = self.get_session(session_id)
        session.update(data)
        return session

chat_sessions = ChatSession()

def get_db_connection():
    """Create database connection"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except Exception as e:
        print(f"Database connection error: {e}")
        return None

def init_db():
    """Initialize database tables"""
    conn = get_db_connection()
    if conn:
        cur = conn.cursor()
        try:
            # Drop old constraint if needed (safe migration)
            cur.execute('''
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.table_constraints 
                        WHERE constraint_name = 'messages_conversation_id_fkey'
                    ) THEN
                        ALTER TABLE messages DROP CONSTRAINT messages_conversation_id_fkey;
                    END IF;
                END$$;
            ''')

            # Create conversations table with conversation_id as PRIMARY KEY
            cur.execute('''
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id VARCHAR(50) PRIMARY KEY,
                    session_id VARCHAR(100),
                    brand VARCHAR(100),
                    model VARCHAR(100),
                    year INTEGER,
                    spare_part_name VARCHAR(200),
                    reference VARCHAR(100),
                    user_phone VARCHAR(20),
                    user_email VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Create messages table referencing conversation_id
            cur.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id SERIAL PRIMARY KEY,
                    conversation_id VARCHAR(50) NOT NULL,
                    role VARCHAR(10),
                    content TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (conversation_id) 
                        REFERENCES conversations(conversation_id) 
                        ON DELETE CASCADE
                )
            ''')

            # Create products table if not exists
            cur.execute('''
                CREATE TABLE IF NOT EXISTS products (
                    id SERIAL PRIMARY KEY,
                    internal_reference VARCHAR(100),
                    product_name VARCHAR(200),
                    product_description TEXT,
                    car_brands TEXT,
                    car_models TEXT,
                    quantity_on_hand INTEGER DEFAULT 0,
                    sales_price DECIMAL(10, 2),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            conn.commit()
            print("✅ Database tables initialized successfully (conversation_id is PK)")
        except Exception as e:
            print(f"❌ Error initializing database: {e}")
            conn.rollback()
        finally:
            cur.close()
            conn.close()


def save_conversation_data(session):
    """Save or update conversation data in database"""
    conn = get_db_connection()
    if not conn:
        return False
    
    cur = conn.cursor()
    try:
        cur.execute('''
            INSERT INTO conversations (
                conversation_id, session_id, brand, model, year,
                spare_part_name, reference, user_phone, user_email
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (conversation_id) DO UPDATE SET
                brand = EXCLUDED.brand,
                model = EXCLUDED.model,
                year = EXCLUDED.year,
                spare_part_name = EXCLUDED.spare_part_name,
                reference = EXCLUDED.reference,
                user_phone = EXCLUDED.user_phone,
                user_email = EXCLUDED.user_email,
                updated_at = CURRENT_TIMESTAMP
        ''', (
            session['conversation_id'],
            session['id'],
            session.get('brand'),
            session.get('model'),
            session.get('year'),
            session.get('spare_part'),
            session.get('reference'),
            session.get('user_phone'),
            session.get('user_email')
        ))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error saving conversation: {e}")
        conn.rollback()
        return False
    finally:
        cur.close()
        conn.close()

def save_message(conversation_id, role, content):
    """Save message to database"""
    conn = get_db_connection()
    if not conn:
        return
    
    cur = conn.cursor()
    try:
        cur.execute('''
            INSERT INTO messages (conversation_id, role, content)
            VALUES (%s, %s, %s)
        ''', (conversation_id, role, content))
        conn.commit()
    except Exception as e:
        print(f"Error saving message: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

def call_deepseek_api(prompt, max_tokens=300, temperature=0.1):
    """Enhanced DeepSeek API call with comprehensive automotive knowledge"""
    try:
        headers = {
            'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
            'Content-Type': 'application/json'
        }
        
        data = {
            'model': 'deepseek-chat',
            'messages': [
                {
                    'role': 'system',
                    'content': '''You are an expert automotive AI with comprehensive knowledge of ALL car brands, models, and spare parts worldwide.
                    You have access to your full knowledge base and can search online if needed.
                    Your knowledge includes:
                    - All global car manufacturers (Toyota, Honda, BMW, Mercedes-Benz, Audi, Volkswagen, Ford, Chevrolet, Nissan, Hyundai, Kia, Peugeot, Renault, Citroen, Fiat, Alfa Romeo, Ferrari, Lamborghini, Porsche, Mazda, Subaru, Mitsubishi, Suzuki, Dacia, Skoda, SEAT, Opel, Vauxhall, Volvo, Saab, Jaguar, Land Rover, Bentley, Rolls-Royce, Aston Martin, McLaren, Bugatti, Koenigsegg, Pagani, Tesla, Rivian, Lucid, BYD, NIO, Xpeng, Li Auto, Great Wall, Geely, Chery, JAC, BAIC, Dongfeng, FAW, GAC, SAIC, Tata, Mahindra, Maruti Suzuki, and many more)
                    - All their models and variants
                    - All types of automotive spare parts
                    You MUST use your FULL knowledge, not a limited list. If unsure, you can search online or use your extensive training data.'''
                },
                {
                    'role': 'user',
                    'content': prompt
                }
            ],
            'max_tokens': max_tokens,
            'temperature': temperature
        }
        
        response = requests.post(DEEPSEEK_API_URL, headers=headers, json=data, timeout=15)
        
        if response.status_code == 200:
            result = response.json()
            return result['choices'][0]['message']['content'].strip()
        else:
            print(f"DeepSeek API error: {response.status_code} - {response.text}")
            return None
    except requests.exceptions.Timeout:
        print("DeepSeek API timeout")
        return None
    except Exception as e:
        print(f"Error calling DeepSeek API: {e}")
        return None
KNOWN_BRANDS = {
    'toyota', 'honda', 'bmw', 'mercedes', 'audi', 'volkswagen', 'ford',
    'chevrolet', 'nissan', 'hyundai', 'kia', 'peugeot', 'renault',
    'citroen', 'fiat', 'alfa romeo', 'ferrari', 'lamborghini', 'porsche',
    'mazda', 'subaru', 'mitsubishi', 'suzuki', 'dacia', 'skoda',
    'seat', 'opel', 'vauxhall', 'volvo', 'jaguar', 'land rover',
    'tesla', 'byd', 'nio', 'xpeng', 'great wall', 'geely', 'chery'
}
def validate_and_correct_brand(user_input):
    # 1) DB-first
    exact_or_best, _ = db_lookup_brand(user_input)
    if exact_or_best:
        # If we found something in our DB inventory, consider it VALID immediately
        return 'VALID', exact_or_best

    # 2) Fallback to DeepSeek only if DB had nothing
    prompt = f"Is '{user_input}' a valid car brand? Respond ONLY in format: VALID|exact_name, SUGGESTION|corrected_name, INVALID|unknown"
    result = call_deepseek_api(prompt, max_tokens=50)
    if result and '|' in result:
        status, brand = result.split('|', 1)
        return status.strip().upper(), brand.strip()
    # Final fallback: treat as suggestion = user input (don’t block flow)
    return 'SUGGESTION', user_input.strip()

def get_known_models_for_brand(brand):
    """Fetch all known models for a brand from the database"""
    conn = get_db_connection()
    if not conn:
        return []
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT DISTINCT UNNEST(STRING_TO_ARRAY(car_models, ',')) AS model
            FROM products
            WHERE car_brands ILIKE %s
        """, (f"%{brand}%",))
        models = [row[0].strip() for row in cur.fetchall() if row[0]]
        return list(set(models))  # deduplicate
    except Exception as e:
        print(f"Error fetching models: {e}")
        return []
    finally:
        cur.close()
        conn.close()
def validate_and_correct_model(user_input, brand):
    """Hybrid model validation: DB + Fuzzy + AI"""
    user_input = user_input.strip()

    # Step 1: Get known models from DB
    known_models = get_known_models_for_brand(brand)
    if not known_models:
        # If no models in DB, fall back to AI
        status, model = call_deepseek_model_validation(user_input, brand)
        return status, model

    # Step 2: Fuzzy match
    matches = process.extract(user_input, known_models, scorer=fuzz.WRatio, limit=3)
    best_match, score = matches[0]

    if score >= 90:  # High confidence direct match
        return 'VALID', best_match
    elif score >= 75:  # Likely typo
        return 'SUGGESTION', best_match
    else:
        # Not found locally — use AI to check global knowledge
        status, model = call_deepseek_model_validation(user_input, brand)
        return status, model

def call_deepseek_model_validation(user_input, brand):
    """Call DeepSeek only when DB fails"""
    prompt = f"""
    Is '{user_input}' a valid {brand} car model? Consider all variants and markets.
    Respond ONLY in format:
    VALID|exact_name
    SUGGESTION|corrected_name
    INVALID|unknown
    """
    result = call_deepseek_api(prompt, max_tokens=50)
    if result and '|' in result:
        status, model = result.split('|', 1)
        return status.strip().upper(), model.strip()
    return 'SUGGESTION', user_input
def validate_and_correct_spare_part(user_input):
    user_input = user_input.strip()

    # 1) DB-first lookup
    exact_or_best, candidates = db_lookup_spare_part(user_input)
    if exact_or_best:
        return 'VALID', exact_or_best

    # 2) Check common variants
    common_parts = {
        'brake pad': ['brake pads', 'break pad', 'break pads'],
        'oil filter': ['oil filt', 'oill filter'],
        'alternator': ['alternatr', 'alternator generator'],
        'timing belt': ['timeing belt', 'cam belt'],
        'spark plug': ['spark plugs'],
        'windshield': ['front glass', 'wind screen']
    }

    for correct, variants in common_parts.items():
        if user_input.lower() == correct or user_input.lower() in variants:
            return 'VALID', correct.title()
        if fuzz.WRatio(user_input, correct) > 85:
            return 'SUGGESTION', correct.title()

    # 3) Fuzzy match against DB candidates
    if candidates:
        matches = process.extract(user_input, candidates, scorer=fuzz.WRatio, limit=1)
        best_match, score = matches[0]
        if score >= 85:
            return 'SUGGESTION', best_match

    # 4) Fallback to AI
    prompt = f"User said '{user_input}'. Is this a car spare part? Respond: VALID|name, SUGGESTION|name"
    result = call_deepseek_api(prompt, max_tokens=50)
    if result and '|' in result:
        status, part = result.split('|', 1)
        return status.strip().upper(), part.strip()

    return 'SUGGESTION', user_input.title()


def get_reference_details(reference, brand, model):
    """Use DeepSeek to get detailed information about a reference number"""
    prompt = f"""
    Given:
    - Reference/Part Number: {reference}
    - Vehicle Brand: {brand}
    - Vehicle Model: {model}
    
    Task: Provide details about this automotive part reference.
    If you recognize this reference pattern, identify what type of part it typically represents.
    
    Provide in format:
    PART_TYPE|part_name|description
    
    If unknown, respond:
    UNKNOWN|generic_part|Part reference {reference} for {brand} {model}
    
    Use your knowledge of common OEM part numbering systems.
    """
    
    result = call_deepseek_api(prompt, max_tokens=100)
    
    if result and '|' in result:
        parts = result.split('|')
        if len(parts) >= 3:
            return {
                'type': parts[0],
                'name': parts[1],
                'description': parts[2]
            }
    
    return {
        'type': 'UNKNOWN',
        'name': 'Spare Part',
        'description': f'Reference: {reference}'
    }


def search_products(brand=None, model=None, spare_part=None, reference=None):
    """Search products in database. When 'reference' is provided, try normalized match
    (remove spaces/dashes) and raw match to avoid misses caused by formatting differences."""
    conn = get_db_connection()
    if not conn:
        return []
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        query = "SELECT * FROM products WHERE 1=1"
        params = []

        if reference:
            # normalize reference (remove non-alnum) for searching normalized stored refs
            clean_ref = re.sub(r'[^A-Za-z0-9]', '', reference)
            query += (
                " AND (REPLACE(REPLACE(internal_reference, ' ', ''), '-', '') ILIKE %s "
                "OR internal_reference ILIKE %s)"
            )
            params.extend([f"%{clean_ref}%", f"%{reference}%"])
        else:
            if brand:
                query += " AND car_brands ILIKE %s"
                params.append(f"%{brand}%")
            if model:
                query += " AND car_models ILIKE %s"
                params.append(f"%{model}%")
            if spare_part:
                query += " AND (product_name ILIKE %s OR product_description ILIKE %s OR internal_reference ILIKE %s)"
                params.extend([f"%{spare_part}%", f"%{spare_part}%", f"%{spare_part}%"])

        query += " LIMIT 10"
        cur.execute(query, params)
        return cur.fetchall()
    except Exception as e:
        print(f"Error searching products: {e}")
        return []
    finally:
        cur.close()
        conn.close()
        

def deepseek_reference_lookup(reference: str, brand: str = "", model: str = ""):
    """
    Strict DeepSeek lookup for a part reference.
    Returns a dict: {status: 'PART'|'UNKNOWN', part: str, description: str, confidence: int}
    """
    # sanitize brand/model (avoid passing tuples or weird values into prompt)
    brand = (brand[1] if isinstance(brand, (list, tuple)) and len(brand) > 1 else str(brand or "")).strip()
    model = (model[1] if isinstance(model, (list, tuple)) and len(model) > 1 else str(model or "")).strip()

    prompt = f"""
You are an automotive spare parts catalog expert. The user provided an OEM reference: "{reference}".
Task:
- If you know the exact part this OEM reference maps to, respond in ONE LINE, EXACTLY in this format (no extra text or explanation):
  PART|<part_name>|<short_description>|CONFIDENCE|<0-100>
  Example:
  PART|Front Brake Pads|Front brake pads for BMW front axle|CONFIDENCE|95

- If you are NOT SURE (confidence < 80) or you don't know, respond exactly:
  UNKNOWN|Reference not found|CONFIDENCE|0

Important:
- Do NOT output any other text.
- Do NOT output tokens like VALID|, SUGGESTION| etc.
- If you reference brand/model in the answer, prefer the provided brand/model info.
"""

    result = call_deepseek_api(prompt, max_tokens=120)
    if not result:
        return {"status": "UNKNOWN", "part": None, "description": "", "confidence": 0, "raw": result}

    raw = result.strip()

    # parse in a defensive way
    parts = [p.strip() for p in raw.split('|') if p is not None]
    # Expected patterns:
    # ['PART', '<part_name>', '<short_description>', 'CONFIDENCE', '95']
    # OR ['UNKNOWN', 'Reference not found', 'CONFIDENCE', '0']
    try:
        if len(parts) >= 5 and parts[0].upper() == 'PART':
            # flexible parse: last token might be confidence or parts[-2]=='CONFIDENCE'
            # prefer to find an integer at the end
            conf = None
            try:
                conf = int(parts[-1])
            except:
                # try parts[-2] label
                if parts[-2].upper() == 'CONFIDENCE':
                    try:
                        conf = int(parts[-1])
                    except:
                        conf = 0
            if conf is None:
                conf = 0
            name = parts[1]
            desc = " | ".join(parts[2:-2]) if len(parts) > 4 else parts[2]
            if conf >= 80:
                return {"status": "PART", "part": name, "description": desc, "confidence": conf, "raw": raw}
            else:
                return {"status": "UNKNOWN", "part": None, "description": "", "confidence": conf, "raw": raw}
        elif len(parts) >= 3 and parts[0].upper() == 'UNKNOWN':
            # safe unknown
            return {"status": "UNKNOWN", "part": None, "description": "", "confidence": 0, "raw": raw}
    except Exception as e:
        print("Error parsing DeepSeek response:", e, raw)

    # if format didn't match, treat as unknown (safe)
    return {"status": "UNKNOWN", "part": None, "description": "", "confidence": 0, "raw": raw}
def validate_and_correct_reference(reference: str, brand: str = "", model: str = ""):
    """
    Check DB first for reference. If not found, call strict DeepSeek lookup.
    Returns:
      - if DB found: {"status":"VALID","source":"DB","results": [...]}
      - if DeepSeek found: {"status":"PART","source":"DeepSeek","part": "...", "description":"...", "confidence": int}
      - otherwise: {"status":"UNKNOWN", "source":"DeepSeek"}
    """
    reference = reference.strip()
    # 1) DB lookup (uses the improved search_products)
    db_results = search_products(reference=reference)
    if db_results:
        return {"status": "VALID", "source": "DB", "results": db_results}

    # 2) DeepSeek fallback (strict)
    ds = deepseek_reference_lookup(reference, brand, model)
    if ds["status"] == "PART":
        return {
            "status": "PART",
            "source": "DeepSeek",
            "part": ds["part"],
            "description": ds["description"],
            "confidence": ds["confidence"],
            "raw": ds.get("raw")
        }

    # 3) unknown
    return {"status": "UNKNOWN", "source": "DeepSeek"}

def process_message(message, session):
    """Process user message with enhanced AI validation"""
    state = session['state']
    response = {'reply': '', 'data': None, 'suggestions': [], 'type': 'text'}
    
    # Welcome state
    if state == 'welcome':
        if any(word in message.lower() for word in ['search', 'spare', 'part', 'find', 'look']):
            session['state'] = 'ask_brand'
            response['reply'] = "🚗 **Let's find the perfect spare part for your vehicle!**\n\n**Step 1: Vehicle Brand**\nPlease tell me your vehicle's brand (e.g., Toyota, BMW, Mercedes, Peugeot, etc.)\n\n💡 I know all car brands worldwide - just type what you have!"
            save_conversation_data(session)
        else:
            response['reply'] = "👋 **Welcome to IMOBOT - Your Intelligent Spare Parts Assistant!**\n\n🔧 I can help you find any spare part for any vehicle!\n\nHow can I assist you today?"
            response['suggestions'] = ['Search Parts', 'Track Order (Soon)', 'Report (Soon)']
    
    # Ask for brand
    elif state == 'ask_brand':
        brand_input = message.strip()
        
        # Use DeepSeek's full knowledge to validate brand
        status, corrected_brand = validate_and_correct_brand(brand_input)
        
        if status == 'VALID':
            session['temp_data']['brand'] = corrected_brand
            session['state'] = 'confirm_brand'
            response['reply'] = f"✅ **Excellent!** I found **{corrected_brand}** in my database.\n\n**Is this correct?**"
            response['suggestions'] = ['Yes', 'No']
        elif status == 'SUGGESTION':
            session['temp_data']['brand'] = corrected_brand
            session['state'] = 'confirm_brand'
            response['reply'] = f"🤔 Did you mean **{corrected_brand}**?\n\n**Please confirm:**"
            response['suggestions'] = ['Yes', 'No']
        else:
            response['reply'] = f"❌ I couldn't recognize '{brand_input}' as a car brand.\n\n**Please enter a valid car brand.**\n\n💡 Examples: Toyota, BMW, Mercedes-Benz, Volkswagen, Peugeot, Renault, Hyundai, Nissan, Ford, etc."
    
    # Confirm brand
    elif state == 'confirm_brand':
        if 'yes' in message.lower():
            session['brand'] = session['temp_data']['brand']
            session['state'] = 'ask_model'
            response['reply'] = f"✅ **Great! {session['brand']} confirmed.**\n\n**Step 2: Vehicle Model**\nNow, what's your {session['brand']} model?\n\n💡 Just type your model name!"
            save_conversation_data(session)
        else:
            session['state'] = 'ask_brand'
            session['temp_data'] = {}
            response['reply'] = "**No problem!** Let's try again.\n\n**Please enter your vehicle brand:**"
    
    # Ask for model
    elif state == 'ask_model':
        model_input = message.strip()
        
        # Use DeepSeek's full knowledge to validate model
        status, corrected_model = validate_and_correct_model(model_input, session['brand'])
        
        if status == 'VALID':
            session['temp_data']['model'] = corrected_model
            session['state'] = 'confirm_model'
            response['reply'] = f"✅ **Perfect!** {session['brand']} **{corrected_model}** found.\n\n**Is this correct?**"
            response['suggestions'] = ['Yes', 'No']
        elif status == 'SUGGESTION':
            session['temp_data']['model'] = corrected_model
            session['state'] = 'confirm_model'
            response['reply'] = f"🤔 Did you mean {session['brand']} **{corrected_model}**?\n\n**Please confirm:**"
            response['suggestions'] = ['Yes', 'No']
        else:
            response['reply'] = f"❌ I couldn't find '{model_input}' as a {session['brand']} model.\n\n**Please enter a valid {session['brand']} model:**"
    
    # Confirm model
    elif state == 'confirm_model':
        if 'yes' in message.lower():
            session['model'] = session['temp_data']['model']
            session['state'] = 'ask_year'
            response['reply'] = f"? **Excellent!** {session['brand']} {session['model']} confirmed.\n\n**Step 3: Vehicle Year (Optional)**\n?? What year is your vehicle?\n?? Enter a year (e.g., 2020) or type 'skip'"
            response['suggestions'] = ['Skip']
            save_conversation_data(session)  # ✅ Save now
        else:
            session['state'] = 'ask_model'
            session['temp_data'] = {}
            response['reply'] = f"**No problem!** Let's try again.\n\n**Please enter your {session['brand']} model:**"
    
    # Ask for year
    elif state == 'ask_year':
        if 'skip' in message.lower():
            session['state'] = 'ask_search_type'
            response['reply'] = "**How would you like to search for your spare part?**\n\n🔍 Choose your search method:"
            response['suggestions'] = ['Search by Reference', 'Search by Part Name']
        else:
            year_match = re.search(r'\b(19|20)\d{2}\b', message)
            if year_match:
                year = int(year_match.group())
                if 1950 <= year <= 2025:
                    session['year'] = year
                    session['state'] = 'ask_search_type'
                    response['reply'] = f"✅ **Year {year} noted!**\n\n**How would you like to search?**"
                    response['suggestions'] = ['Search by Reference', 'Search by Part Name']
                    save_conversation_data(session)
                else:
                    response['reply'] = "Please enter a valid year (1950-2025) or 'skip':"
                    response['suggestions'] = ['Skip']
            else:
                response['reply'] = "Please enter a valid year or 'skip':"
                response['suggestions'] = ['Skip']
    
    # Ask search type
    elif state == 'ask_search_type':
        if 'reference' in message.lower():
            session['search_type'] = 'reference'
            session['state'] = 'ask_reference'
            response['reply'] = "📋 **Reference Number Search**\n\nPlease enter the part reference number (OEM number, part code, etc.):"
        elif 'part' in message.lower() or 'name' in message.lower():
            session['search_type'] = 'part_name'
            session['state'] = 'ask_part_name'
            response['reply'] = "🔧 **Part Name Search**\n\nWhat spare part are you looking for?\n\n💡 Examples: brake pads, oil filter, alternator, timing belt, spark plugs..."
        else:
            response['reply'] = "Please choose your search method:"
            response['suggestions'] = ['Search by Reference', 'Search by Part Name']
    
    # Ask for reference
    elif state == 'ask_reference':
        reference_input = message.strip().upper()
        session['temp_data']['reference'] = reference_input

        # sanitize brand/model values (avoid passing wrong types into prompts)
        brand_val = session.get('brand') or ""
        if isinstance(brand_val, (list, tuple)):
            brand_val = brand_val[1] if len(brand_val) > 1 else brand_val[0]
        brand_val = str(brand_val)

        model_val = session.get('model') or ""
        if isinstance(model_val, (list, tuple)):
            model_val = model_val[1] if len(model_val) > 1 else model_val[0]
        model_val = str(model_val)

        # 1) DB-first search (normalized)
        products = search_products(reference=reference_input)

        if products:
            product = products[0]
            session['temp_data']['product'] = product
            session['state'] = 'confirm_reference'
            response['reply'] = (
                f"✅ **Found in our catalog!**\n\n"
                f"📋 **Reference**: {reference_input}\n"
                f"🚗 **Vehicle**: {brand_val} {model_val}\n"
                f"🔧 **Part**: {product.get('product_name')}\n"
                f"📝 **Description**: {product.get('product_description')}\n\n"
                f"**Is this what you're looking for?**"
            )
            response['suggestions'] = ['Yes', 'No']
            response['type'] = 'parts'
            response['data'] = products[:3]
        else:
            # 2) Strict DeepSeek fallback
            ds = validate_and_correct_reference(reference_input, brand=brand_val, model=model_val)
            session['state'] = 'confirm_reference'
            if ds.get('status') == 'PART':
                # Use the AI result but mark clearly as an external lookup with confidence
                response['reply'] = (
                    f"📋 **Reference**: {reference_input}\n"
                    f"🚗 **Vehicle**: {brand_val} {model_val}\n"
                    f"🔧 **Part Type (external lookup)**: {ds.get('part')}\n"
                    f"📝 **Description**: {ds.get('description')}\n"
                    f"⚠️ Confidence: {ds.get('confidence')}%\n\n"
                    "Is this the correct reference?"
                )
                response['suggestions'] = ['Yes', 'No']
            else:
                # Safe: unknown — don't show random guesses
                response['reply'] = (
                    f"📋 **Reference**: {reference_input}\n"
                    f"🚗 **Vehicle**: {brand_val} {model_val}\n\n"
                    "❌ I couldn't find a confident match for this reference.\n"
                    "Would you like to provide the part name or leave your phone so our agents can help?"
                )
                response['suggestions'] = ['Provide Part Name', 'Share Phone']



    
    # Ask for part name
    elif state == 'ask_part_name':
        part_input = message.strip()
        
        # Use DeepSeek to understand the part
        status, corrected_part = validate_and_correct_spare_part(part_input)
        
        session['temp_data']['spare_part'] = corrected_part
        session['state'] = 'confirm_part'
        
        if status == 'VALID':
            response['reply'] = f"✅ Looking for **{corrected_part}**\n\n**Is this correct?**"
        else:
            response['reply'] = f"🔧 Are you searching for **{corrected_part}**?\n\n**Please confirm:**"
        
        response['suggestions'] = ['Yes', 'No']
    
    # Confirm part
    elif state == 'confirm_part':
        if 'yes' in message.lower():
            session['spare_part'] = session['temp_data']['spare_part']

            products = search_products(
                brand=session.get('brand'),
                model=session.get('model'),
                spare_part=session['spare_part']
            )

            if products:
                # ✅ Found in DB → show details + order
                product = products[0]
                price = product.get('sales_price', 0)
                session['state'] = 'ask_order'
                response['reply'] = (
                    f"🔧 **Part**: {product.get('product_name')}\n"
                    f"🚗 **For**: {session['brand']} {session['model']}\n"
                    f"💰 **Price**: {price} DZD\n\n"
                    f"**Ready to order?**"
                )
                response['type'] = 'parts'
                response['data'] = products[:3]
                response['suggestions'] = ['Order Now', 'Continue Shopping']
            else:
                # ❌ Not in DB → ask for contact directly (no mention of missing part)
                session['state'] = 'ask_contact'
                response['reply'] = (
                    "📱 Please share your phone number so one of our agents can assist you:"
                )

            save_conversation_data(session)
        else:
            session['state'] = 'ask_part_name'
            session['temp_data'] = {}
            response['reply'] = "**Let's try again.** What spare part are you looking for?"

    # Ask for order confirmation
    elif state == 'ask_order':
        if 'order' in message.lower() or 'yes' in message.lower():
            session['state'] = 'ask_contact'
            response['reply'] = "📱 **Excellent! Let's complete your order.**\n\nPlease provide your phone number:"
        elif 'continue' in message.lower() or 'shop' in message.lower():
            session['state'] = 'welcome'
            response['reply'] = "**No problem!** How else can I help you?"
            response['suggestions'] = ['Search Parts']
        else:
            response['reply'] = "Would you like to order or continue shopping?"
            response['suggestions'] = ['Order Now', 'Continue Shopping']
    
    # Ask for contact
    elif state == 'ask_contact':
        phone_match = re.search(r'[\d\s\+\-\(\)]{8,}', message)
        
        if phone_match:
            session['user_phone'] = phone_match.group().strip()
            session['state'] = 'ask_email'
            response['reply'] = "📧 **Thank you!**\n\nPlease provide your email address (or type 'skip'):"
            response['suggestions'] = ['Skip']
            save_conversation_data(session)
        else:
            response['reply'] = "Please enter a valid phone number:"
# Ask for email (continuing from where the code left off)
    elif state == 'ask_email':
        if 'skip' in message.lower():
            session['state'] = 'complete_order'
            response['reply'] = "🎉 **Perfect! Your request has been submitted successfully!**\n\n📋 **Order Summary:**\n" + \
                              f"🚗 **Vehicle**: {session.get('brand', '')} {session.get('model', '')} {session.get('year', '')}\n" + \
                              (f"🔧 **Part**: {session.get('spare_part', '')}\n" if session.get('spare_part') else '') + \
                              (f"📋 **Reference**: {session.get('reference', '')}\n" if session.get('reference') else '') + \
                              f"📱 **Phone**: {session.get('user_phone', '')}\n\n" + \
                              "✅ **Our team will contact you within 24 hours with availability and pricing!**\n\n" + \
                              "Thank you for choosing our service!"
            response['suggestions'] = ['Search More Parts', 'Start New Search']
            save_conversation_data(session)
        else:
            email_match = re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', message)
            if email_match:
                session['user_email'] = email_match.group()
                session['state'] = 'complete_order'
                response['reply'] = "🎉 **Excellent! Your request has been completed!**\n\n📋 **Order Summary:**\n" + \
                                  f"🚗 **Vehicle**: {session.get('brand', '')} {session.get('model', '')} {session.get('year', '')}\n" + \
                                  (f"🔧 **Part**: {session.get('spare_part', '')}\n" if session.get('spare_part') else '') + \
                                  (f"📋 **Reference**: {session.get('reference', '')}\n" if session.get('reference') else '') + \
                                  f"📱 **Phone**: {session.get('user_phone', '')}\n" + \
                                  f"📧 **Email**: {session.get('user_email', '')}\n\n" + \
                                  "✅ **Our team will contact you within 24 hours!**\n\n" + \
                                  "Thank you for choosing our service!"
                response['suggestions'] = ['Search More Parts', 'Start New Search']
                save_conversation_data(session)
            else:
                response['reply'] = "Please enter a valid email address or type 'skip':"
                response['suggestions'] = ['Skip']
    
    # Complete order state
    elif state == 'complete_order':
        if 'search' in message.lower() or 'more' in message.lower():
            # Reset session for new search but keep contact info
            contact_info = {
                'user_phone': session.get('user_phone'),
                'user_email': session.get('user_email')
            }
            session_id_local = session['id']
            session.clear()
            session['id'] = session_id_local
            session['state'] = 'ask_brand'
            session['temp_data'] = {}
            session['conversation_id'] = str(uuid.uuid4())
            session.update(contact_info)  # Keep contact info for easier reordering
            
            response['reply'] = "🚗 **Let's find another part for you!**\n\n**Step 1: Vehicle Brand**\nPlease tell me your vehicle's brand:"
        elif 'new' in message.lower():
            # Complete reset
            session_id = session['id']
            session.clear()
            session['id'] = session_id
            session['state'] = 'welcome'
            session['temp_data'] = {} 
            session['conversation_id'] = str(uuid.uuid4())
            
            response['reply'] = "👋 **Welcome back to IMOBOT!**\n\nHow can I help you today?"
            response['suggestions'] = ['Search Parts']
        else:
            response['reply'] = "How else can I help you?"
            response['suggestions'] = ['Search More Parts', 'Start New Search']
    
    # Handle unknown states
    else:
        session['state'] = 'welcome'
        response['reply'] = "I seem to have lost track of our conversation. Let's start fresh!\n\nHow can I help you today?"
        response['suggestions'] = ['Search Parts']
    
    return response
# --- Add these helpers (near other DB helpers) ---
def db_lookup_brand(user_input: str):
    conn = get_db_connection()
    if not conn:
        return None, []
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        q = """
            SELECT DISTINCT car_brands FROM products
            WHERE car_brands ILIKE %s
            LIMIT 20
        """
        cur.execute(q, (f"%{user_input}%",))
        rows = cur.fetchall()
        # car_brands may be a comma-separated list; split & normalize
        candidates = set()
        for r in rows:
            if r['car_brands']:
                for b in re.split(r'[,/;|]', r['car_brands']):
                    b=b.strip()
                    if b: candidates.add(b)
        # Prefer exact-insensitive match first
        exact = [b for b in candidates if b.lower()==user_input.lower()]
        if exact:
            return exact[0], list(candidates)
        # Otherwise best fuzzy contains match
        return (next(iter(candidates)) if candidates else None, list(candidates))
    finally:
        cur.close(); conn.close()

def db_lookup_model(brand: str, user_input: str):
    conn = get_db_connection()
    if not conn:
        return None, []
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        q = """
            SELECT DISTINCT car_models, car_brands FROM products
            WHERE car_brands ILIKE %s AND car_models ILIKE %s
            LIMIT 50
        """
        cur.execute(q, (f"%{brand}%", f"%{user_input}%"))
        rows = cur.fetchall()
        candidates = set()
        for r in rows:
            if r['car_models']:
                for m in re.split(r'[,/;|]', r['car_models']):
                    m=m.strip()
                    if m: candidates.add(m)
        exact = [m for m in candidates if m.lower()==user_input.lower()]
        if exact:
            return exact[0], list(candidates)
        return (next(iter(candidates)) if candidates else None, list(candidates))
    finally:
        cur.close(); conn.close()
def db_lookup_spare_part(user_input: str):
    """Search for a spare part name directly in the DB"""
    conn = get_db_connection()
    if not conn:
        return None, []
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        q = """
            SELECT DISTINCT product_name 
            FROM products
            WHERE product_name ILIKE %s OR product_description ILIKE %s
            LIMIT 20
        """
        cur.execute(q, (f"%{user_input}%", f"%{user_input}%"))
        rows = cur.fetchall()
        
        candidates = {r['product_name'] for r in rows if r['product_name']}
        
        # Prefer exact-insensitive match first
        exact = [p for p in candidates if p.lower() == user_input.lower()]
        if exact:
            return exact[0], list(candidates)
        
        return (next(iter(candidates)) if candidates else None, list(candidates))
    finally:
        cur.close()
        conn.close()

@app.route('/')
def index():
    """Serve the main chat interface"""
    return render_template('index.html')

@app.route('/api/chat', methods=['POST'])
def chat():
    """Enhanced chat endpoint with better error handling"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        message = data.get('message', '').strip()
        session_id = data.get('sessionId', str(uuid.uuid4()))
        
        if not message:
            return jsonify({'error': 'Empty message'}), 400
        
        # Get or create session
        session = chat_sessions.get_session(session_id)
        
        # Save user message
        save_message(session['conversation_id'], 'user', message)
        
        
        save_conversation_data(session)

        
        
        # Process message with enhanced AI
        response = process_message(message, session)
        
        # Save bot response
        save_message(session['conversation_id'], 'bot', response.get('reply', ''))
        
        # Update session
        chat_sessions.update_session(session_id, session)
        
        return jsonify({
            'reply': response.get('reply', 'I apologize, but I could not process your request.'),
            'suggestions': response.get('suggestions', []),
            'data': response.get('data'),
            'type': response.get('type', 'text'),
            'sessionId': session_id
        })
    
    except Exception as e:
        print(f"❌ Chat endpoint error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'reply': 'I encountered an error processing your request. Please try again.',
            'suggestions': ['Try Again'],
            'error': str(e)
        }), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'database': 'connected' if get_db_connection() else 'disconnected'
    })

@app.route('/api/conversations', methods=['GET'])
def get_conversations():
    """Get conversation history for admin/analytics"""
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Get conversations with message counts
        cur.execute('''
            SELECT c.*, COUNT(m.id) as message_count
            FROM conversations c
            LEFT JOIN messages m ON c.conversation_id = m.conversation_id
            GROUP BY c.id
            ORDER BY c.created_at DESC
            LIMIT 100
        ''')
        
        conversations = cur.fetchall()
        
        return jsonify({
            'conversations': conversations,
            'total': len(conversations)
        })
    
    except Exception as e:
        print(f"Error fetching conversations: {e}")
        return jsonify({'error': 'Failed to fetch conversations'}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/api/conversation/<conversation_id>', methods=['GET'])
def get_conversation_details(conversation_id):
    """Get detailed conversation with all messages"""
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Get conversation details
        cur.execute('SELECT * FROM conversations WHERE conversation_id = %s', (conversation_id,))
        conversation = cur.fetchone()
        
        if not conversation:
            return jsonify({'error': 'Conversation not found'}), 404
        
        # Get all messages
        cur.execute('''
            SELECT * FROM messages 
            WHERE conversation_id = %s 
            ORDER BY created_at ASC
        ''', (conversation_id,))
        
        messages = cur.fetchall()
        
        return jsonify({
            'conversation': conversation,
            'messages': messages
        })
    
    except Exception as e:
        print(f"Error fetching conversation details: {e}")
        return jsonify({'error': 'Failed to fetch conversation details'}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Get system statistics"""
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        stats = {}
        
        # Total conversations
        cur.execute('SELECT COUNT(*) as total FROM conversations')
        stats['total_conversations'] = cur.fetchone()['total']
        
        # Total messages
        cur.execute('SELECT COUNT(*) as total FROM messages')
        stats['total_messages'] = cur.fetchone()['total']
        
        # Conversations today
        cur.execute('SELECT COUNT(*) as total FROM conversations WHERE DATE(created_at) = CURRENT_DATE')
        stats['conversations_today'] = cur.fetchone()['total']
        
        # Most searched brands
        cur.execute('''
            SELECT brand, COUNT(*) as count 
            FROM conversations 
            WHERE brand IS NOT NULL 
            GROUP BY brand 
            ORDER BY count DESC 
            LIMIT 10
        ''')
        stats['top_brands'] = cur.fetchall()
        
        # Most searched parts
        cur.execute('''
            SELECT spare_part_name, COUNT(*) as count 
            FROM conversations 
            WHERE spare_part_name IS NOT NULL 
            GROUP BY spare_part_name 
            ORDER BY count DESC 
            LIMIT 10
        ''')
        stats['top_parts'] = cur.fetchall()
        
        return jsonify(stats)
    
    except Exception as e:
        print(f"Error fetching stats: {e}")
        return jsonify({'error': 'Failed to fetch stats'}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/api/products', methods=['GET'])
def get_products():
    """Get products with pagination and search"""
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Get query parameters
        page = int(request.args.get('page', 1))
        limit = min(int(request.args.get('limit', 20)), 100)  # Max 100 items
        search = request.args.get('search', '').strip()
        
        offset = (page - 1) * limit
        
        # Build query
        base_query = "SELECT * FROM products"
        count_query = "SELECT COUNT(*) as total FROM products"
        params = []
        
        if search:
            search_condition = '''
                WHERE product_name ILIKE %s 
                OR internal_reference ILIKE %s 
                OR car_brands ILIKE %s 
                OR car_models ILIKE %s
            '''
            base_query += search_condition
            count_query += search_condition
            search_param = f"%{search}%"
            params = [search_param, search_param, search_param, search_param]
        
        # Get total count
        cur.execute(count_query, params)
        total = cur.fetchone()['total']
        
        # Get products
        cur.execute(f"{base_query} ORDER BY created_at DESC LIMIT %s OFFSET %s", 
                   params + [limit, offset])
        products = cur.fetchall()
        
        return jsonify({
            'products': products,
            'pagination': {
                'page': page,
                'limit': limit,
                'total': total,
                'pages': (total + limit - 1) // limit
            }
        })
    
    except Exception as e:
        print(f"Error fetching products: {e}")
        return jsonify({'error': 'Failed to fetch products'}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/admin')
def admin_dashboard():
    """Simple admin dashboard"""
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>IMOBOT Admin Dashboard</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }
            .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                     color: white; padding: 30px; border-radius: 10px; margin-bottom: 30px; }
            .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); 
                    gap: 20px; margin-bottom: 30px; }
            .stat-card { background: white; padding: 25px; border-radius: 10px; 
                        box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            .stat-number { font-size: 2.5em; font-weight: bold; color: #667eea; }
            .stat-label { color: #666; margin-top: 10px; }
            .section { background: white; padding: 25px; border-radius: 10px; 
                      box-shadow: 0 2px 10px rgba(0,0,0,0.1); margin-bottom: 20px; }
            .btn { background: #667eea; color: white; padding: 10px 20px; 
                  border: none; border-radius: 5px; cursor: pointer; margin-right: 10px; }
            .btn:hover { background: #5a6fd8; }
            table { width: 100%; border-collapse: collapse; margin-top: 20px; }
            th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
            th { background: #f8f9fa; }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>🤖 IMOBOT Admin Dashboard</h1>
            <p>Monitor conversations, performance, and system health</p>
        </div>
        
        <div class="stats" id="stats">
            <div class="stat-card">
                <div class="stat-number" id="totalConversations">-</div>
                <div class="stat-label">Total Conversations</div>
            </div>
            <div class="stat-card">
                <div class="stat-number" id="totalMessages">-</div>
                <div class="stat-label">Total Messages</div>
            </div>
            <div class="stat-card">
                <div class="stat-number" id="conversationsToday">-</div>
                <div class="stat-label">Conversations Today</div>
            </div>
            <div class="stat-card">
                <div class="stat-number" id="systemHealth">🟢</div>
                <div class="stat-label">System Health</div>
            </div>
        </div>
        
        <div class="section">
            <h2>📊 Quick Actions</h2>
            <button class="btn" onclick="loadStats()">🔄 Refresh Stats</button>
            <button class="btn" onclick="loadConversations()">💬 View Conversations</button>
            <button class="btn" onclick="loadProducts()">📦 View Products</button>
            <button class="btn" onclick="exportData()">📥 Export Data</button>
        </div>
        
        <div class="section">
            <h2>📈 Recent Activity</h2>
            <div id="recentActivity">Loading...</div>
        </div>
        
        <script>
            async function loadStats() {
                try {
                    const response = await fetch('/api/stats');
                    const stats = await response.json();
                    
                    document.getElementById('totalConversations').textContent = stats.total_conversations || 0;
                    document.getElementById('totalMessages').textContent = stats.total_messages || 0;
                    document.getElementById('conversationsToday').textContent = stats.conversations_today || 0;
                } catch (error) {
                    console.error('Error loading stats:', error);
                }
            }
            
            async function loadConversations() {
                try {
                    const response = await fetch('/api/conversations');
                    const data = await response.json();
                    
                    let html = '<table><tr><th>ID</th><th>Brand</th><th>Model</th><th>Part</th><th>Phone</th><th>Created</th></tr>';
                    
                    data.conversations.forEach(conv => {
                        html += `<tr>
                            <td>${conv.conversation_id.substring(0, 8)}...</td>
                            <td>${conv.brand || '-'}</td>
                            <td>${conv.model || '-'}</td>
                            <td>${conv.spare_part_name || conv.reference || '-'}</td>
                            <td>${conv.user_phone || '-'}</td>
                            <td>${new Date(conv.created_at).toLocaleDateString()}</td>
                        </tr>`;
                    });
                    
                    html += '</table>';
                    document.getElementById('recentActivity').innerHTML = html;
                } catch (error) {
                    document.getElementById('recentActivity').innerHTML = 'Error loading conversations';
                }
            }
            
            async function loadProducts() {
                try {
                    const response = await fetch('/api/products');
                    const data = await response.json();
                    
                    let html = '<table><tr><th>Reference</th><th>Name</th><th>Brands</th><th>Price</th><th>Stock</th></tr>';
                    
                    data.products.forEach(product => {
                        html += `<tr>
                            <td>${product.internal_reference || '-'}</td>
                            <td>${product.product_name || '-'}</td>
                            <td>${product.car_brands || '-'}</td>
                            <td>${product.sales_price || 0} DZD</td>
                            <td>${product.quantity_on_hand || 0}</td>
                        </tr>`;
                    });
                    
                    html += '</table>';
                    document.getElementById('recentActivity').innerHTML = html;
                } catch (error) {
                    document.getElementById('recentActivity').innerHTML = 'Error loading products';
                }
            }
            
            function exportData() {
                // Simple export functionality
                window.open('/api/conversations?format=json', '_blank');
            }
            
            // Load initial stats
            loadStats();
            loadConversations();
            
            // Refresh stats every 30 seconds
            setInterval(loadStats, 30000);
        </script>
    </body>
    </html>
    '''

if __name__ == '__main__':
    print("🚀 Starting IMOBOT Spare Parts Assistant...")
    print("=" * 50)
    
    # Initialize database
    print("📊 Initializing database...")
    init_db()
    
    # Test DeepSeek API connection
    print("🤖 Testing DeepSeek API connection...")
    test_response = call_deepseek_api("Test connection", max_tokens=10)
    if test_response:
        print("✅ DeepSeek API connected successfully")
    else:
        print("❌ DeepSeek API connection failed - check your API key")
    
    # Test database connection
    print("🗄️  Testing database connection...")
    test_conn = get_db_connection()
    if test_conn:
        print("✅ Database connected successfully")
        test_conn.close()
    else:
        print("❌ Database connection failed - check your configuration")
    
    print("=" * 50)
    print("🌐 Starting Flask server...")
    print("📱 Chat Interface: http://localhost:5000")
    print("👑 Admin Dashboard: http://localhost:5000/admin")
    print("🔧 API Health: http://localhost:5000/api/health")
    print("=" * 50)
    
    # Run the Flask app
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=True,
        threaded=True
    )
