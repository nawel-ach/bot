
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
                    found BOOLEAN DEFAULT FALSE,   -- ✅ new column
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 
                        FROM information_schema.columns 
                        WHERE table_name='conversations' 
                        AND column_name='found'
                    ) THEN
                        ALTER TABLE conversations ADD COLUMN found BOOLEAN DEFAULT FALSE;
                    END IF;
                END$$;
            """)
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
                spare_part_name, reference, user_phone, user_email, found
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (conversation_id) DO UPDATE SET
                brand = EXCLUDED.brand,
                model = EXCLUDED.model,
                year = EXCLUDED.year,
                spare_part_name = EXCLUDED.spare_part_name,
                reference = EXCLUDED.reference,
                user_phone = EXCLUDED.user_phone,
                user_email = EXCLUDED.user_email,
                found = EXCLUDED.found,
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
            session.get('user_email'),
            session.get('found', False)   # ✅ Save found status
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




def validate_and_correct_brand_model_year(user_input: str):
    """
    Extract brand, model, and year from user input.
    Supports English, French, Arabic, and free-form sentences.
    Prioritizes DB lookup, fallback to DeepSeek with strict schema.
    Returns: (status, brand, model, year)
    """
    raw_input = user_input.strip()

    # --- 1) DB lookup first ---
    # Try to find a brand inside input
    for known_brand in KNOWN_BRANDS:
        if known_brand.lower() in raw_input.lower():
            brand = known_brand.title()
            # Try to get model for this brand
            models = get_known_models_for_brand(brand)
            for model in models:
                if model.lower() in raw_input.lower():
                    # Extract year if present (only valid 1950-2025)
                    year_match = re.search(r'(19[5-9]\d|20[0-2]\d|2025)', raw_input)
                    year_val = int(year_match.group()) if year_match else None
                    return "VALID", brand, model, year_val

    # --- 2) Fallback: DeepSeek strict extraction ---
    prompt = f"""
    Act as an automotive assistant AI.
    The user said: "{raw_input}"

    Task: Identify the vehicle brand, model, and year.
    - Brand must be an official automotive manufacturer (global knowledge, e.g., Toyota, Peugeot, مرسيدس, رينو, بي ام دبليو).
    - Model must be the vehicle's commercial model (e.g., Corolla, 3008, C200).
    - Year must be between 1950 and 2025 if present.
    - Support English, French, Arabic, or mixed sentences.
    - If unsure, suggest the closest valid values.

    Respond ONLY in this exact format:
    VALID|<brand>|<model>|<year or NONE>
    SUGGESTION|<brand>|<model>|<year or NONE>
    INVALID|unknown|unknown|unknown

    Examples:
    Input: "Hello, I need Peugeot 3008 2019"
    Output: VALID|Peugeot|3008|2019

    Input: "مرسيدس سي 200 موديل 2015"
    Output: VALID|Mercedes-Benz|C200|2015

    Input: "I want Honda"
    Output: SUGGESTION|Honda|unknown|NONE
    """

    result = call_deepseek_api(prompt, max_tokens=80)
    if not result:
        return "INVALID", None, None, None

    parts = [p.strip() for p in result.split("|")]
    if len(parts) < 4:
        return "INVALID", None, None, None

    status, brand, model, year_str = parts[0].upper(), parts[1], parts[2], parts[3]

    # Normalize year
    year_val = None
    if year_str and year_str not in ("NONE", "UNKNOWN"):
        try:
            year_val = int(year_str)
            if not (1950 <= year_val <= 2025):
                year_val = None
        except:
            year_val = None

    # Final return
    return status, brand if brand != "unknown" else None, model if model != "unknown" else None, year_val



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
def validate_and_correct_spare_part(user_input: str):
    """
    Validate and normalize spare part names (multi-language: EN, FR, AR).
    Returns: (status, part_name)
    - status: VALID | SUGGESTION | INVALID
    - part_name: Clean corrected spare part name
    """
    user_input = user_input.strip()

    # --- 1) DB-first lookup ---
    exact_or_best, candidates = db_lookup_spare_part(user_input)
    if exact_or_best:
        return "VALID", exact_or_best.title()

    # --- 2) Common multilingual variants ---
    common_parts = {
        "brake pads": ["brake pad", "plaquette de frein", "فرامل", "break pads"],
        "oil filter": ["filtre à huile", "oil filt", "فلتر زيت"],
        "air filter": ["filtre à air", "فلتر هواء"],
        "fuel filter": ["filtre à carburant", "فلتر وقود"],
        "alternator": ["alternatr", "alternator generator", "alternateur", "الدينامو"],
        "timing belt": ["courroie de distribution", "timeing belt", "cam belt", "سير الكاتينة"],
        "spark plug": ["spark plugs", "bougie d’allumage", "شمعة"],
        "windshield": ["pare-brise", "front glass", "wind screen", "زجاج أمامي"],
        "clutch kit": ["kit embrayage", "طقم دبرياج"]
    }

    lower_in = user_input.lower()
    for correct, variants in common_parts.items():
        if lower_in == correct or lower_in in variants:
            return "VALID", correct.title()
        if fuzz.WRatio(lower_in, correct) > 87:
            return "SUGGESTION", correct.title()

    # --- 3) Fuzzy match against DB candidates ---
    if candidates:
        matches = process.extract(user_input, candidates, scorer=fuzz.WRatio, limit=1)
        best_match, score = matches[0]
        if score >= 88:
            return "SUGGESTION", best_match.title()

    # --- 4) Fallback: DeepSeek strict check ---
    prompt = f"""
    You are an expert automotive spare-parts assistant.
    User input: "{user_input}"

    Task:
    - Identify if this is a car spare part.
    - If yes: Respond ONLY as "VALID|<clean part name>"
    - If it's unclear but close: "SUGGESTION|<corrected part name>"
    - If not a part: "INVALID|unknown"

    Rules:
    - Output must be a single line with exactly one '|'.
    - The part name must be short and clean (e.g., "Brake Pads", "Oil Filter").
    - Do not include brand, model, or extra details.
    """

    result = call_deepseek_api(prompt, max_tokens=40)
    if result and "|" in result:
        try:
            status, part = result.split("|", 1)
            return status.strip().upper(), part.strip().title()
        except:
            pass

    # --- 5) Last fallback: return as suggestion ---
    return "SUGGESTION", user_input.title()




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
        


VALID_PART_KEYWORDS = [
    "brake", "filter", "clutch", "alternator", "belt", "pump", "injector",
    "radiator", "suspension", "mirror", "light", "headlamp", "shock",
    "bearing", "piston", "valve", "gasket", "sensor", "turbo"
]

def is_valid_part_name(name: str) -> bool:
    n = name.lower()
    return any(kw in n for kw in VALID_PART_KEYWORDS)


def validate_and_correct_reference(reference: str, brand: str = "", model: str = ""):
    """
    Validate reference number:
    - If in DB → return its product name(s).
    - If not → directly ask for phone number (no DeepSeek fallback, no guessing).
    """
    reference = reference.strip()

    # 1) Check in DB
    db_results = search_products(reference=reference)
    if db_results:
        product_names = [row["product_name"] for row in db_results if row.get("product_name")]
        return {
            "status": "VALID",
            "source": "DB",
            "results": product_names
        }

    # 2) Not in DB → ask phone (no DeepSeek guessing)
    return {
        "status": "NOT_FOUND",
        "source": "DB",
        "message": "📱 Please provide your phone number so our agents can assist you."
    }




def process_message(message, session):
    """Process user message with enhanced AI validation"""
    state = session['state']
    response = {'reply': '', 'data': None, 'suggestions': [], 'type': 'text'}
    
    # Welcome state
    if state == 'welcome':
        if any(word in message.lower() for word in ['search', 'spare', 'part', 'find', 'look']):
            session['state'] = 'ask_vehicle'
            response['reply'] = (
            "🚗 **Let's find your spare part!**\n\n"
            "Please enter your vehicle brand, model, and year together:\n\n"
            "💡 Examples:\n"
            "- Toyota Corolla 2018\n"
            "- BMW X5 2020\n"
            "- Mercedes C200 2015"
             )
            save_conversation_data(session)
        else:
            response['reply'] = "👋 **Welcome to IMOBOT - Your Intelligent Spare Parts Assistant!**\n\n🔧 I can help you find any spare part for any vehicle!\n\nHow can I assist you today?"
            response['suggestions'] = ['Search Parts', 'Track Order (Soon)', 'Report (Soon)']
    
    # Ask for brand
    elif state == 'ask_vehicle':
        v_input = message.strip()
        status, brand, model, year = validate_and_correct_brand_model_year(v_input)

        if status == "VALID":
            session['brand'] = brand
            session['model'] = model
            session['year'] = year
            session['state'] = 'ask_search_type'
            response['reply'] = f"✅ Vehicle confirmed: **{brand} {model} {year or ''}**\n\nHow would you like to search for your spare part?"
            response['suggestions'] = ['Search by Reference', 'Search by Part Name']
            save_conversation_data(session)

        elif status == "SUGGESTION":
            session['temp_data']['brand'] = brand
            session['temp_data']['model'] = model
            session['temp_data']['year'] = year
            session['state'] = 'confirm_vehicle'
            response['reply'] = f"🤔 Did you mean **{brand} {model} {year or ''}**?"
            response['suggestions'] = ['Yes', 'No']

        else:
            response['reply'] = (
                f"❌ I couldn't recognize '{v_input}' as a valid vehicle.\n\n"
                "Please enter brand, model, and year (e.g., 'Toyota Corolla 2018')."
            )
    elif state == 'confirm_vehicle':
        if 'yes' in message.lower():
            session['brand'] = session['temp_data']['brand']
            session['model'] = session['temp_data']['model']
            session['year'] = session['temp_data']['year']
            session['state'] = 'ask_search_type'
            response['reply'] = f"✅ Vehicle confirmed: **{session['brand']} {session['model']} {session['year'] or ''}**\n\nHow would you like to search?"
            response['suggestions'] = ['Search by Reference', 'Search by Part Name']
            save_conversation_data(session)
        else:
            session['state'] = 'ask_vehicle'
            session['temp_data'] = {}
            response['reply'] = "❌ No worries! Please re-enter your vehicle brand, model, and year (e.g., 'Honda Civic 2017')."

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
        reference_input = message.strip()
        display_ref = reference_input.upper()
        session['temp_data']['reference'] = display_ref

        brand_val = sanitize_session_value(session.get('brand'))
        model_val = sanitize_session_value(session.get('model'))

        # ✅ DB-first and only
        products = search_products(reference=reference_input)

        if products:
            product = products[0]
            session['temp_data']['product'] = product
            session['state'] = 'confirm_reference'
            response['reply'] = (
                f"✅ **Found in our catalog!**\n\n"
                f"📋 **Reference**: {display_ref}\n"
                f"🚗 **Vehicle**: {brand_val} {model_val}\n"
                f"🔧 **Part**: {product.get('product_name')}\n"
                f"📝 **Description**: {product.get('product_description')}\n\n"
                f"**Is this what you're looking for?**"
            )
            response['suggestions'] = ['Yes', 'No']
            response['type'] = 'parts'
            response['data'] = products[:3]
        else:
            # ❌ Not in DB → skip AI lookup, ask phone immediately
            session['state'] = 'ask_contact'
            response['reply'] = (
                f"📋 **Reference**: {display_ref}\n"
                f"🚗 **Vehicle**: {brand_val} {model_val}\n\n"
                "📱 Please provide your phone number so our agents can assist you."
            )
            response['suggestions'] = []

        return response


    elif state == 'confirm_reference':
        m = message.strip().lower()
        yes_re = re.compile(r'\b(yes|y|oui|si|ok|confirm|sure)\b')
        no_re  = re.compile(r'\b(no|n|non|cancel|wrong|not)\b')

        if yes_re.search(m):
            session['reference'] = session['temp_data'].get('reference')
            product = session['temp_data'].get('product')

            if product:
                session['found'] = True
                # ✅ Save spare part name into session (will go to DB)
                session['spare_part'] = product.get('product_name')
                session['spare_part_name'] = product.get('product_name')

                price = product.get('sales_price', 0)
                session['state'] = 'ask_order'
                response['reply'] = (
                    f"📦 **Part**: {product.get('product_name')}\n"
                    f"💰 **Price**: {price} DZD\n\n"
                    f"**Would you like to order now?**"
                )
                response['type'] = 'parts'
                response['data'] = [product]
                response['suggestions'] = ['Order Now', 'Continue Shopping']
            else:
                # Normally won't reach here since only DB products reach confirm_reference
                session['found'] = False
                session['state'] = 'ask_contact'
                response['reply'] = "📱 Please share your phone number so one of our agents can assist you:"
                response['suggestions'] = []

            # ✅ Persist to DB (spare_part_name will now be stored)
            save_conversation_data(session)
            return response

        elif no_re.search(m):
            session['state'] = 'ask_reference'
            session['temp_data'] = {}
            response['reply'] = "**No worries.** Please re-enter the correct reference number:"
            response['suggestions'] = []
            return response

        else:
            response['reply'] = "Please reply with 'Yes' or 'No'."
            response['suggestions'] = ['Yes', 'No']
            return response


    
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
                session['found'] = True
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
                session['found'] = False
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
        
        
def sanitize_session_value(val):
    """Return a clean single string from session values that may contain AI tokens
    or lists/tuples. E.g. 'BMW, SUGGESTION|BMW...' -> 'BMW'."""
    if not val:
        return ""
    if isinstance(val, (list, tuple)):
        # prefer first element (usually the human-friendly one)
        val = val[0] if len(val) > 0 else ""
    v = str(val).strip()
    # remove any AI token fragments or trailing commas after first clean token
    # split on '|' or ',' and take first non-empty part
    parts = [p.strip() for p in re.split(r'[\|,]', v) if p.strip()]
    return parts[0] if parts else v

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
            GROUP BY c.conversation_id
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
            async function loadHealth() {
                try {
                    const response = await fetch('/api/health');
                    const data = await response.json();
                    document.getElementById('systemHealth').textContent =
                        data.database === 'connected' ? '🟢' : '🔴';
                } catch (error) {
                    document.getElementById('systemHealth').textContent = '⚠️';
                }
            }

            // call it once + refresh every 30s
            loadHealth();
            setInterval(loadHealth, 30000);

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
