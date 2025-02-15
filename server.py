from flask import Flask, request, jsonify, send_from_directory, render_template,session
import os
import requests
import ifcopenshell
from flask_cors import CORS
import google.generativeai as genai
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
UPLOAD_FOLDER = "uploads"
app.secret_key = "your_secret_key" 
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
CORS(app)

genai.configure(api_key="AIzaSyDwQiUCckcW54Gh2UP8ccNcZwEErjxUOOU")

# Gemini AI model configuration
generation_config = {
    "temperature": 0.7,
    "top_p": 0.9,
    "top_k": 50,
    "max_output_tokens": 1000,
    "response_mime_type": "text/plain",
}

model = genai.GenerativeModel(
    model_name="gemini-2.0-flash-exp",
    generation_config=generation_config,
)
# Constants for material calculations
BRICK_VOLUME_M3 = 0.0016929
BRICK_VOLUME_FT3 = BRICK_VOLUME_M3 * 35.315
M3_TO_FT3 = 35.315
M2_TO_FT2 = 10.764

# Constants for concrete mix ratio (1:2:4)
CEMENT_RATIO = 1
SAND_RATIO = 2
AGGREGATE_RATIO = 4
TOTAL_RATIO = CEMENT_RATIO + SAND_RATIO + AGGREGATE_RATIO
DRY_TO_WET_VOLUME_CONVERSION = 1.54  # Factor to account for dry-to-wet volume conversion

# Constants for material densities (kg/m³)
CEMENT_DENSITY = 1440  # kg/m³
SAND_DENSITY = 1600    # kg/m³

HISTORICAL_PRICES = {"brick": 5, "cement": 300, "sand": 50}
CURRENT_PRICES = {"brick": 6, "cement": 320, "sand": 55}

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

def calculate_material_quantitiess(elements, material_type):
    material_data = {"total_volume_m3": 0, "total_volume_ft3": 0, "count": 0}
    for element in elements:
        if hasattr(element, "IsDefinedBy") and element.IsDefinedBy:
            for rel in element.IsDefinedBy:
                if hasattr(rel, "RelatingPropertyDefinition"):
                    prop_def = rel.RelatingPropertyDefinition
                    if hasattr(prop_def, "Quantities"):
                        for qty in prop_def.Quantities:
                            if qty.Name == "NetVolume":
                                volume_m3 = getattr(qty, "VolumeValue", 0)
                                material_data["total_volume_m3"] += volume_m3
                                material_data["total_volume_ft3"] += volume_m3 * M3_TO_FT3
                                if material_type == "brick":
                                    material_data["count"] += (volume_m3 * M3_TO_FT3) / BRICK_VOLUME_FT3
    return material_data

def calculate_cement_and_sands(volume_m3):
    dry_volume_m3 = volume_m3 * DRY_TO_WET_VOLUME_CONVERSION
    cement_volume_m3 = (CEMENT_RATIO / TOTAL_RATIO) * dry_volume_m3
    sand_volume_m3 = (SAND_RATIO / TOTAL_RATIO) * dry_volume_m3
    return {
        "cement_volume_m3": cement_volume_m3,
        "cement_weight_kg": cement_volume_m3 * CEMENT_DENSITY / 50,  # Cement in 50kg bags
        "sand_volume_m3": sand_volume_m3,
        "sand_weight_kg": sand_volume_m3 * SAND_DENSITY,
    }

def calculate_costss(material_quantities):
    return {
        "brick": {
            "quantity": material_quantities.get("bricks", {}).get("count", 0),
            "past_cost": HISTORICAL_PRICES["brick"] * material_quantities["bricks"]["count"],
            "current_cost": CURRENT_PRICES["brick"] * material_quantities["bricks"]["count"],
        },
        "cement": {
            "quantity": material_quantities["concrete"]["cement_weight_kg"],
            "past_cost": HISTORICAL_PRICES["cement"] * material_quantities["concrete"]["cement_weight_kg"],
            "current_cost": CURRENT_PRICES["cement"] * material_quantities["concrete"]["cement_weight_kg"],
        },
        "sand": {
            "quantity": material_quantities["concrete"]["sand_volume_m3"],
            "past_cost": HISTORICAL_PRICES["sand"] * material_quantities["concrete"]["sand_volume_m3"],
            "current_cost": CURRENT_PRICES["sand"] * material_quantities["concrete"]["sand_volume_m3"],
        },
    }

def parse_ifc_with_materialss(file_path):
    model = ifcopenshell.open(file_path)
    elements = {
        "walls": model.by_type("IfcWall"),
        "slabs": model.by_type("IfcSlab"),
        "columns": model.by_type("IfcColumn"),
        "beams": model.by_type("IfcBeam"),
    }

    material_quantities = {
        "bricks": calculate_material_quantitiess(elements["walls"], "brick"),
        "concrete": calculate_cement_and_sands(
            sum(calculate_material_quantitiess(elements[etype], "concrete")["total_volume_m3"]
                for etype in ["slabs", "columns", "beams"])
        ),
    }

    return {
        "material_quantities": material_quantities,
        "costs": calculate_costss(material_quantities),
    }


def calculate_cement_and_sand_in_kg(volume_m3):
    """
    Calculate the cement and sand required in kilograms for a given volume of concrete in m³.
    """
    dry_volume_m3 = volume_m3 * DRY_TO_WET_VOLUME_CONVERSION
    cement_volume_m3 = (CEMENT_RATIO / TOTAL_RATIO) * dry_volume_m3
    sand_volume_m3 = (SAND_RATIO / TOTAL_RATIO) * dry_volume_m3

    # Convert volumes to weights
    cement_weight_kg = (cement_volume_m3 * CEMENT_DENSITY)//50
    sand_weight_kg = sand_volume_m3 * SAND_DENSITY

    return {
        "cement_volume_m3": cement_volume_m3,
        "cement_Bag_50kg": cement_weight_kg,
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

    # Add cement and sand quantities (volume and weight)
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

@app.route('/')
def index():
    return render_template('index.html')

@app.route("/MaterialAnalysis", methods=["GET", "POST"])
def upload_file():
    if request.method == "POST":
        if "file" not in request.files:
            return jsonify({"error": "No file part"})
        file = request.files["file"]
        if file.filename == "":
            return jsonify({"error": "No selected file"})
        if file and file.filename.endswith(".ifc"):
            file_path = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
            file.save(file_path)
            result = parse_ifc_with_materials(file_path)

            # Filter and restructure the result to remove zero values
            filtered_result = {
                material: data for material, data in result.items()
                if any(value > 0 for key, value in data.items() if isinstance(value, (int, float)))
            }

            # Restructure to display material names first
            structured_result = []
            for material, data in filtered_result.items():
                structured_result.append({"material": material, "description": data})

            return jsonify(structured_result)
        else:
            return jsonify({"error": "Only .ifc files are supported"})
    return render_template("your_template.html")

@app.route("/fetch-prices-api", methods=["GET"])
def fetch_prices_api():
    prices = fetch_live_chennai_prices()  # Use the Selenium scraper function here
    return jsonify(prices)
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

@app.route("/comparision", methods=["GET", "POST"])
def upload_files():
    if request.method == "POST":
        if "file" not in request.files:
            return jsonify({"error": "No file part"}), 400
        file = request.files["file"]
        if file.filename == "":
            return jsonify({"error": "No selected file"}), 400
        if file and file.filename.endswith(".ifc"):
            file_path = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
            file.save(file_path)

            # Parse the IFC file and generate analysis
            result = parse_ifc_with_materialss(file_path)
            session["analysis_data"] = result["costs"]  # Store analysis data in the session

            return render_template("analysis.html", costs=result["costs"])
        return jsonify({"error": "Only .ifc files are supported"}), 400
    return render_template("index1.html")


@app.route("/chat", methods=["POST"])
def chat():
    """
    Handle chatbot interactions for "what-if" scenarios using analysis data.
    """
    user_message = request.json.get("message", "").strip()
    if not user_message:
        return jsonify({"error": "No message provided."}), 400

    # Retrieve the stored analysis data
    analysis_data = session.get("analysis_data", {})
    if not analysis_data:
        return jsonify({"error": "No analysis data available. Please upload an IFC file first."}), 400

    try:
        # Use Gemini AI to generate a response with the analysis data as context
        chat_session = model.start_chat(history=[])
        input_message = (
            f"Using the following analysis data:\n{analysis_data}\n"
            f"Respond to the user's query: {user_message}"
        )
        response = chat_session.send_message(input_message)
        return jsonify({"response": response.text})
    except Exception as e:
        return jsonify({"error": f"Failed to generate response: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(debug=True)
