from flask import Flask, request, jsonify, render_template, session
import ifcopenshell
import os
import google.generativeai as genai
from flask_cors import CORS


# Configure Flask application
app = Flask(__name__)
app.secret_key = "your_secret_key"  # For session handling
UPLOAD_FOLDER = "uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
CORS(app)
# Configure Gemini AI
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

CEMENT_RATIO = 1
SAND_RATIO = 2
AGGREGATE_RATIO = 4
TOTAL_RATIO = CEMENT_RATIO + SAND_RATIO + AGGREGATE_RATIO
DRY_TO_WET_VOLUME_CONVERSION = 1.54  # Factor to account for dry-to-wet volume conversion

CEMENT_DENSITY = 1440  # kg/m³
SAND_DENSITY = 1600    # kg/m³

HISTORICAL_PRICES = {"brick": 5, "cement": 300, "sand": 50}
CURRENT_PRICES = {"brick": 6, "cement": 320, "sand": 55}


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


@app.route("/", methods=["GET", "POST"])
def upload_file():
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
    return render_template("index.html")


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
