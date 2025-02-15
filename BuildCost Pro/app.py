from flask import Flask, request, jsonify, send_from_directory, render_template
import sqlite3
import os
import requests
import ifcopenshell
from flask_cors import CORS
from data_extraction import parse_ifc_with_materials
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Material Constants
BRICK_VOLUME_M3 = 0.0016929
BRICK_VOLUME_FT3 = BRICK_VOLUME_M3 * 35.315
M3_TO_FT3 = 35.315
M2_TO_FT2 = 10.764

# Concrete mix ratio (1:2:4)
CEMENT_RATIO = 1
SAND_RATIO = 2
AGGREGATE_RATIO = 4
TOTAL_RATIO = CEMENT_RATIO + SAND_RATIO + AGGREGATE_RATIO

# Dry-to-wet volume conversion
DRY_TO_WET_VOLUME_CONVERSION = 1.54

# Material densities (kg/m³)
CEMENT_DENSITY = 1440
SAND_DENSITY = 1600
AGGREGATE_DENSITY = 1450
STEEL_DENSITY = 7850

# Static historical data
HISTORICAL_DATA = {
    "brick": 45,  # Average price per unit (₹)
    "concrete": 100,
    "steel": 150,
    "glass": 200,
}


# SQLite database setup
def init_db():
    with sqlite3.connect('materials.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                material TEXT NOT NULL,
                unit_price REAL NOT NULL,
                source TEXT NOT NULL
            )
        ''')
        conn.commit()

init_db()

@app.route('/')
def index():
    return render_template('index.html')

# Serve the prediction page
@app.route('/prediction',methods=['POST','GET'])
def prediction():
   
        return render_template('your_template.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if file and allowed_file(file.filename):
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        file.save(filepath)

        # Process the file (e.g., parse 3D model and extract materials)
        try:
            materials = parse_ifc_with_materials(filepath)  # Placeholder for your IFC parser
            print("Parsed materials:", materials)  # Debugging: Log parsed materials
        except Exception as e:
            return jsonify({'error': f"Failed to process file: {str(e)}"}), 500

       
        real_time_prices = fetch_real_time_prices()
        real_time_cost = calculate_real_time_cost(materials)
        

        # Fetch historical prices and calculate costs (assuming calculate_historical_cost exists)
        try:
            historical_cost = calculate_historical_cost(materials)
        except Exception as e:
            return jsonify({'error': f"Failed to calculate historical costs: {str(e)}"}), 500

        # Generate comparison
        print(real_time_cost)
        comparison = generate_comparison(real_time_cost, historical_cost)
        print(comparison)
        return jsonify({
            'real_time_prices': real_time_prices,  # Include raw prices for display
            'real_time_cost': real_time_cost,
            'historical_cost': historical_cost,
            'comparison': comparison
        })
    else:
        return jsonify({'error': 'Invalid file type'}), 400

def calculate_material_quantities(elements, material_type):
    material_data = {
        "total_volume_m3": 0,
        "total_volume_ft3": 0,
        "total_area_m2": 0,
        "total_area_ft2": 0,
        "count": 0
    }

    for element in elements:
        if hasattr(element, "IsDefinedBy") and element.IsDefinedBy:
            for rel in element.IsDefinedBy:
                if hasattr(rel, "RelatingPropertyDefinition"):
                    property_definition = rel.RelatingPropertyDefinition
                    if hasattr(property_definition, "Quantities"):
                        for quantity in property_definition.Quantities:
                            if hasattr(quantity, "Name"):
                                if quantity.Name == "NetVolume":
                                    volume_m3 = getattr(quantity, "VolumeValue", 0)
                                    volume_ft3 = volume_m3 * M3_TO_FT3
                                    material_data["total_volume_m3"] += volume_m3
                                    material_data["total_volume_ft3"] += volume_ft3
                                    if material_type == "brick":
                                        material_data["count"] += volume_ft3 / BRICK_VOLUME_FT3
                                if quantity.Name in ["GrossArea", "NetArea"]:
                                    area_m2 = getattr(quantity, "AreaValue", 0)
                                    area_ft2 = area_m2 * M2_TO_FT2
                                    material_data["total_area_m2"] += area_m2
                                    material_data["total_area_ft2"] += area_ft2
    return material_data

def calculate_cement_and_sand_in_kg(volume_m3):
    dry_volume_m3 = volume_m3 * DRY_TO_WET_VOLUME_CONVERSION
    cement_volume_m3 = (CEMENT_RATIO / TOTAL_RATIO) * dry_volume_m3
    sand_volume_m3 = (SAND_RATIO / TOTAL_RATIO) * dry_volume_m3

    cement_weight_kg = cement_volume_m3 * CEMENT_DENSITY
    sand_weight_kg = sand_volume_m3 * SAND_DENSITY

    return {
        "cement_volume_m3": cement_volume_m3,
        "cement_weight_kg": cement_weight_kg,
        "sand_volume_m3": sand_volume_m3,
        "sand_weight_kg": sand_weight_kg,
    }

def parse_ifc_with_materials(file_path):
    model = ifcopenshell.open(file_path)
    elements = {
        "walls": model.by_type("IfcWall"),
        "slabs": model.by_type("IfcSlab"),
        "columns": model.by_type("IfcColumn"),
        "beams": model.by_type("IfcBeam"),
        "reinforcements": model.by_type("IfcReinforcingElement"),
        "windows": model.by_type("IfcWindow"),
        "curtain_walls": model.by_type("IfcCurtainWall"),
        "roofs": model.by_type("IfcRoof"),
    }

    concrete_data = {
        "total_volume_m3": sum(
            calculate_material_quantities(elements[etype], "concrete")["total_volume_m3"]
            for etype in ["slabs", "columns", "beams"]
        ),
        "total_volume_ft3": sum(
            calculate_material_quantities(elements[etype], "concrete")["total_volume_ft3"]
            for etype in ["slabs", "columns", "beams"]
        ),
    }

    cement_and_sand = calculate_cement_and_sand_in_kg(concrete_data["total_volume_m3"])
    concrete_data.update(cement_and_sand)

    material_quantities = {
        "bricks": calculate_material_quantities(elements["walls"], "brick"),
        "concrete": concrete_data,
        "steel": calculate_material_quantities(elements["reinforcements"], "steel"),
        "glass": {
            "total_area_m2": sum(
                calculate_material_quantities(elements[etype], "glass")["total_area_m2"]
                for etype in ["windows", "curtain_walls"]
            ),
            "total_area_ft2": sum(
                calculate_material_quantities(elements[etype], "glass")["total_area_ft2"]
                for etype in ["windows", "curtain_walls"]
            ),
        },
        "timber": {
            "total_volume_m3": sum(
                calculate_material_quantities(elements[etype], "timber")["total_volume_m3"]
                for etype in ["beams", "slabs"]
            ),
            "total_volume_ft3": sum(
                calculate_material_quantities(elements[etype], "timber")["total_volume_ft3"]
                for etype in ["beams", "slabs"]
            ),
        },
        "roof": calculate_material_quantities(elements["roofs"], "roof"),
    }
    return material_quantities

def fetch_historical_prices(materials):
    """
    Fetches historical average prices for the given materials from static data.

    Parameters:
        materials (list): List of material names.

    Returns:
        dict: A dictionary with material names as keys and their average prices as values.
    """
    historical_prices = {}
    for material in materials:
        historical_prices[material] = HISTORICAL_DATA.get(material.lower(), 0)
    return historical_prices

def calculate_costs(material_quantities, prices):
    """
    Calculates total costs based on material quantities and unit prices.

    Parameters:
        material_quantities (dict): Quantities of materials (e.g., volumes, areas).
        prices (dict): Material prices (real-time or historical).

    Returns:
        list: A list of dictionaries containing material details and costs.
    """
    costs = []
    for material, data in material_quantities.items():
        quantity = data.get("total_volume_m3", 0)  # Example quantity in cubic meters
        unit_price = prices.get(material.lower(), 0)
        total_cost = quantity * unit_price
        costs.append({
            "material": material,
            "quantity": quantity,
            "unit_price": unit_price,
            "total_cost": total_cost,
        })
    return costs

def generate_comparison(real_time_costs, historical_costs):
    """
    Generates a comparison between real-time and historical costs.

    Parameters:
        real_time_costs (list): List of real-time costs.
        historical_costs (list): List of historical costs.

    Returns:
        list: A list of dictionaries containing the comparison details.
    """
    comparison = []
    for rt, hc in zip(real_time_costs, historical_costs):
        comparison.append({
            "material": rt["material"],
            "real_time_cost": rt["total_cost"],
            "historical_cost": hc["total_cost"],
            "difference": rt["total_cost"] - hc["total_cost"],
        })
    return comparison


def fetch_live_chennai_prices():
    """
    Scrapes material prices dynamically using Selenium from the Live Chennai website.
    """
    url = "https://www.livechennai.com/brick_sand_jalli_price_list_chennai.asp"
    prices = {}

    # Selenium WebDriver setup
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    )

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

    try:
        driver.get(url)
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CLASS_NAME, "table-price"))
        )

        tables = driver.find_elements(By.CLASS_NAME, "table-price")

        for table in tables:
            category = table.find_element(By.TAG_NAME, "tr").text.strip()
            rows = table.find_elements(By.TAG_NAME, "tr")[2:]
            for row in rows:
                cells = row.find_elements(By.TAG_NAME, "td")
                if len(cells) >= 3:
                    material = cells[0].text.strip().lower()
                    price_text = cells[2].text.strip().replace('₹', '').replace(',', '')
                    if " - " in price_text:
                        range_values = price_text.split(" - ")
                        try:
                            lower_price = float(range_values[0].strip())
                            upper_price = float(range_values[1].strip())
                            price = (lower_price + upper_price) / 2
                        except ValueError:
                            price = None
                    else:
                        try:
                            price = float(price_text)
                        except ValueError:
                            price = None

                    if category not in prices:
                        prices[category] = {}
                    prices[category][material] = price

        print("Extracted Prices:", prices)
        return prices

    except Exception as e:
        print(f"Error: {e}")
        return {"error": "Unable to fetch data from the website. Please try again later."}

    finally:
        driver.quit()

@app.route("/fetch-prices-api", methods=["GET"])
def fetch_prices_api():
    prices = fetch_live_chennai_prices()  # Use the Selenium scraper function here
    return jsonify(prices)

@app.route("/comparison", methods=["POST"])
def comparison():
    """
    Handles the comparison of real-time and historical material costs.

    Returns:
        JSON: A comparison of costs.
    """
    # Example input material quantities
    material_quantities = {
        "brick": {"total_volume_m3": 100},
        "concrete": {"total_volume_m3": 50},
    }

    # Fetch prices
    real_time_prices = fetch_real_time_prices()
    historical_prices = fetch_historical_prices(material_quantities.keys())

    # Calculate costs
    real_time_costs = calculate_costs(material_quantities, real_time_prices)
    historical_costs = calculate_costs(material_quantities, historical_prices)

    # Generate comparison
    comparison_result = generate_comparison(real_time_costs, historical_costs)

    return jsonify({
        "real_time_costs": real_time_costs,
        "historical_costs": historical_costs,
        "comparison": comparison_result,
    })

@app.route("/comparison", methods=["GET"])
def comparison_page():
    try:
        # Example material quantities
        material_quantities = {
            "brick": {"total_volume_m3": 100},
            "concrete": {"total_volume_m3": 50},
        }

        # Fetch real-time prices
        real_time_prices = fetch_real_time_prices()

        # Add fallback prices for materials if real-time fetching fails
        fallback_prices = {
            "bricks": 50,
            "concrete": 120,
            "steel": 150,
            "glass": 200,
            "timber": 100,
            "roof": 80,
        }

        # Use real-time prices if available, otherwise fallback
        final_real_time_prices = {
            material: real_time_prices.get(material, fallback_prices[material])
            for material in fallback_prices.keys()
        }

        # Static historical data for comparison
        HISTORICAL_DATA = {
            "brick": 45,
            "concrete": 100,
        }

        # Fetch historical prices
        historical_prices = {key: HISTORICAL_DATA.get(key, 0) for key in material_quantities.keys()}

        # Calculate costs
        real_time_costs = calculate_costs(material_quantities, final_real_time_prices)
        historical_costs = calculate_costs(material_quantities, historical_prices)

        # Debugging: Print the generated data
        print("Real-Time Costs:", real_time_costs)
        print("Historical Costs:", historical_costs)

        # Generate comparison
        comparison_result = generate_comparison(real_time_costs, historical_costs)
        print("Comparison Result:", comparison_result)

        # Render the comparison page
        return render_template(
            "comparison.html",
            real_time_costs=real_time_costs,
            historical_costs=historical_costs,
            comparison=comparison_result,
        )
    except Exception as e:
        print(f"Error generating comparison: {e}")
        return render_template("comparison.html", real_time_costs=[], historical_costs=[], comparison=[])

@app.route("/parsed-materials", methods=["GET"])
def parsed_materials():
    # Path to the fetch_prices.html file
    fetch_prices_path = os.path.join("path_to_html_folder", "fetch_prices.html")

    # Initialize parsed materials dictionary
    parsed_materials_data = {}

    try:
        # Open and parse the fetch_prices.html file
        with open(fetch_prices_path, "r", encoding="utf-8") as file:
            soup = BeautifulSoup(file, "html.parser")

        # Extract material prices using appropriate selectors
        parsed_materials_data = {
            "bricks": {
                "total_volume_m3": 54.48108063161,
                "total_volume_ft3": 1923.9993625053075,
                "price_per_unit": float(soup.select_one("#brick-price").text.strip().replace("₹", "").replace(",", ""))
            },
            "concrete": {
                "total_volume_m3": 77.187428454394,
                "total_volume_ft3": 2725.874035866924,
                "price_per_unit": float(soup.select_one("#concrete-price").text.strip().replace("₹", "").replace(",", ""))
            },
            # Add more materials as needed
        }

    except Exception as e:
        print(f"Error parsing fetch_prices.html: {e}")

    # Pass parsed materials data to the template
    return render_template(
        "your_template.html",
        parsed_materials=parsed_materials_data
    )


@app.route('/fetch-prices', methods=['GET'])
def fetch_prices_page():
    return render_template('fetch_prices.html')

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'ifc', 'obj', 'fbx', 'gltf', 'glb'}

def fetch_real_time_prices():
    """
    Scrapes real-time prices for materials from multiple trusted websites.
    Aggregates and averages the prices from all sources.
    Returns a dictionary with material names as keys and average prices as values.
    """
    def fetch_prices_from_site1():
        """
        Scrapes prices from site 1.
        """
        prices = {}
        url = "https://www.99acres.com/articles/building-materials.html?utm_source=chatgpt.com"  # Replace with actual URL

        try:
            response = requests.get(url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            # Extract prices (adjust selectors based on the website structure)
            prices['bricks'] = float(soup.select_one('#brick-price').text.strip().replace('₹', '').replace(',', ''))
            prices['concrete'] = float(soup.select_one('#concrete-price').text.strip().replace('₹', '').replace(',', ''))
            prices['steel'] = float(soup.select_one('#steel-price').text.strip().replace('₹', '').replace(',', ''))
            prices['glass'] = float(soup.select_one('#glass-price').text.strip().replace('₹', '').replace(',', ''))
            prices['timber'] = float(soup.select_one('#timber-price').text.strip().replace('₹', '').replace(',', ''))
            prices['roof'] = float(soup.select_one('#roof-price').text.strip().replace('₹', '').replace(',', ''))

            print("Fetched real-time prices from site 1:", prices)  # Debugging
        except Exception as e:
            print("Error fetching prices from site 1:", str(e))
        return prices

    def fetch_prices_from_site2():
        """
        Scrapes prices from site 2.
        """
        prices = {}
        url = "https://www.livechennai.com/brick_sand_jalli_price_list_chennai.asp?utm_source=chatgpt.com"  # Replace with actual URL

        try:
            response = requests.get(url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            # Extract prices (adjust selectors based on the website structure)
            prices['bricks'] = float(soup.select_one('.brick-price-class').text.strip().replace('₹', '').replace(',', ''))
            prices['concrete'] = float(soup.select_one('.concrete-price-class').text.strip().replace('₹', '').replace(',', ''))
            prices['steel'] = float(soup.select_one('.steel-price-class').text.strip().replace('₹', '').replace(',', ''))
            prices['glass'] = float(soup.select_one('.glass-price-class').text.strip().replace('₹', '').replace(',', ''))
            prices['timber'] = float(soup.select_one('.timber-price-class').text.strip().replace('₹', '').replace(',', ''))
            prices['roof'] = float(soup.select_one('.roof-price-class').text.strip().replace('₹', '').replace(',', ''))

            print("Fetched real-time prices from site 2:", prices)  # Debugging
        except Exception as e:
            print("Error fetching prices from site 2:", str(e))
        return prices

    # Combine prices from multiple sites
    sources = [fetch_prices_from_site1, fetch_prices_from_site2]
    combined_prices = {}

    for fetcher in sources:
        try:
            prices = fetcher()
            for material, price in prices.items():
                if material not in combined_prices:
                    combined_prices[material] = []
                combined_prices[material].append(price)
        except Exception as e:
            print(f"Error fetching prices from a source: {e}")

    # Calculate average price for each material
    average_prices = {
        material: sum(prices) / len(prices)
        for material, prices in combined_prices.items() if prices
    }

    print("Final aggregated real-time prices:", average_prices)  # Debugging
    return average_prices


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'ifc', 'obj', 'fbx', 'gltf', 'glb'}

@app.route('/add_material', methods=['POST'])
def add_material():
    data = request.get_json()
    material = data.get('material')
    unit_price = data.get('unit_price')
    source = data.get('source')

    if not material or unit_price is None or not source:
        return jsonify({'error': 'Missing required fields'}), 400

    with sqlite3.connect('materials.db') as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT INTO materials (material, unit_price, source) VALUES (?, ?, ?)', (material, unit_price, source))
        conn.commit()
        print(f"Material added: {material}, Unit Price: {unit_price}, Source: {source}")  # Debugging: Log material addition

    return jsonify({'message': 'Material added successfully'}), 201

def parse_3d_model(filepath):
    # Placeholder: Extract material quantities from the 3D model file
    materials = [
        {'material': 'Brick', 'quantity': 100},
        {'material': 'Cement', 'quantity': 50},
        {'material': 'Steel', 'quantity': 30}
    ]
    print("Parsed 3D model materials:", materials)  # Debugging: Log placeholder materials
    return materials

def calculate_real_time_cost(materials):
    # Fetch real-time prices dynamically
    prices = fetch_real_time_prices()

    result = []
    if isinstance(materials, dict):  # If `materials` is a dictionary
        for material, data in materials.items():
            normalized_material = material.lower()  # Normalize material names
            unit_price = prices.get(normalized_material, 0)
            quantity = data.get("total_volume_m3", 0)  # Use total_volume_m3 as quantity
            total = quantity * unit_price
            result.append({'material': material, 'quantity': quantity, 'unit_price': unit_price, 'total': total})
    elif isinstance(materials, list):  # If `materials` is a list
        for item in materials:
            material = item.get('material', 'Unknown')
            quantity = item.get('quantity', 0)
            unit_price = prices.get(material.lower(), 0)
            total = quantity * unit_price
            result.append({'material': material, 'quantity': quantity, 'unit_price': unit_price, 'total': total})
    else:
        raise ValueError("Invalid materials format: Expected list or dictionary")

    print("Real-time costs calculation result:", result)  # Debugging
    return result



def calculate_historical_cost(materials):
    result = []
    with sqlite3.connect('materials.db') as conn:
        cursor = conn.cursor()

        if isinstance(materials, dict):
            for material, data in materials.items():
                quantity = data.get("total_volume_m3", 0)
                cursor.execute('SELECT AVG(unit_price) FROM materials WHERE material = ?', (material,))
                unit_price = cursor.fetchone()[0] or 0
                total = quantity * unit_price
                result.append({'material': material, 'quantity': quantity, 'unit_price': unit_price, 'total': total})
                print(f"Historical cost for {material}: Unit Price: {unit_price}, Total: {total}")  # Debugging
        elif isinstance(materials, list):
            for item in materials:
                material = item.get('material', 'Unknown')
                quantity = item.get('quantity', 0)
                cursor.execute('SELECT AVG(unit_price) FROM materials WHERE material = ?', (material,))
                unit_price = cursor.fetchone()[0] or 0
                total = quantity * unit_price
                result.append({'material': material, 'quantity': quantity, 'unit_price': unit_price, 'total': total})
                print(f"Historical cost for {material}: Unit Price: {unit_price}, Total: {total}")  # Debugging
        else:
            raise ValueError("Invalid materials format: Expected list or dictionary")

    return result

def generate_comparison(real_time_cost, historical_cost):
    comparison = []
    for rt, hc in zip(real_time_cost, historical_cost):
        diff = rt['total'] - hc['total']
        comparison.append(f"{rt['material']}: Real-time cost is {'higher' if diff > 0 else 'lower'} by {abs(diff)}")
    print("Comparison details:", comparison)  # Debugging: Log comparison details
    return '\n'.join(comparison)

if __name__ == '__main__':
    app.run(debug=True)


