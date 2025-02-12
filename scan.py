from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse
import re
import pytesseract
from pdf2image import convert_from_bytes
from PyPDF2 import PdfReader
from io import BytesIO
from typing import Dict, Optional

app = FastAPI()

# Configuración para extracción de Dirección, Email y Teléfono.
FIELD_CONFIG = {
    "Direccion_principal": {
        "patterns": [r"((?:CR|CL|DG|TV|AV|CARRERA|CALLE)\s*\d+.*)"],
        "process": lambda x: re.sub(r"\s+", " ", x.strip())
    },
    "Email": {
        "patterns": [r"([\w\.-]+@[\w\.-]+\.\w{2,})"]
    },
    "Telefono": {
        "patterns": [r"(\b3\d{9}\b)"]
    }
}

def preprocess_text(text: str) -> str:
    """ Normaliza saltos de línea y espacios. """
    text = text.replace('\r\n', '\n')
    text = re.sub(r'\n+', '\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()

def extract_text(pdf_bytes: bytes) -> str:
    """ Extrae el texto del PDF usando PyPDF2 o, si no funciona, aplica OCR. """
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        text = "\n".join([page.extract_text() or '' for page in reader.pages])
        if len(text) > 100:
            return preprocess_text(text)
    except Exception:
        pass
    try:
        images = convert_from_bytes(pdf_bytes, dpi=300)
        text = "\n".join([pytesseract.image_to_string(img, lang='spa') for img in images])
        return preprocess_text(text)
    except Exception as e:
        raise RuntimeError(f"Error OCR: {str(e)}")

def extract_field(text: str, config: dict) -> Optional[str]:
    """ Extrae un campo usando los patrones definidos en FIELD_CONFIG. """
    for pattern in config["patterns"]:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            if "process" in config:
                value = config["process"](value)
            return value
    return None

def extract_nit_and_id_from_filename(filename: str) -> str:
    """ Extrae el NIT y el Número de Identificación desde el nombre del archivo eliminando 'RUT' y '.pdf'. """
    return re.sub(r"RUT|\D", "", filename)

def extract_razon_social(text: str) -> str:
    """ Extrae Razón Social desde la sección correspondiente en el PDF. Si está vacía, devuelve ''. """
    match = re.search(r"35\.\s*Raz[oó]n social\s*\n\s*(.+)", text, re.IGNORECASE)
    if match:
        razon_social = match.group(1).strip()
        # Si lo encontrado es un encabezado como "31. Primer apellido...", ignorarlo y devolver vacío
        if "31. Primer apellido" in razon_social:
            return ""
        return razon_social if razon_social else ""
    return ""


def extract_names(text: str) -> Dict[str, str]:
    """ Extrae Primer Apellido, Segundo Apellido y Primer Nombre desde la línea correcta. """
    pattern = r"^([A-ZÁÉÍÓÚÑ]+)\s+([A-ZÁÉÍÓÚÑ]+)\s+([A-ZÁÉÍÓÚÑ]+(?:\s+[A-ZÁÉÍÓÚÑ]+)*)$"
    for line in text.splitlines():
        line = line.strip()
        if re.match(pattern, line):
            match = re.match(pattern, line)
            return {
                "Primer_apellido": match.group(1),
                "Segundo_apellido": match.group(2),
                "Primer_nombre": match.group(3)
            }
    return {}

def extract_dept_city(text: str) -> Dict[str, str]:
    """ Extrae Departamento y Ciudad desde la línea que contiene 'COLOMBIA'. """
    for line in text.splitlines():
        if "COLOMBIA" in line.upper():
            cleaned = re.sub(r'\d', '', line).strip()
            tokens = cleaned.split()
            if len(tokens) >= 3 and tokens[0].upper() == "COLOMBIA":
                return {"Departamento": tokens[1], "Ciudad": tokens[2]}
    return {}

def smart_extractor(text: str, filename: str) -> Dict[str, Optional[str]]:
    """ Extrae todos los datos del RUT. """
    nit_and_id = extract_nit_and_id_from_filename(filename)  # Extraer NIT y Número de Identificación desde el filename
    results = {
        "NIT": nit_and_id,
        "Numero_identificacion": nit_and_id,  # Mismo valor para Número de Identificación
        "Razon_social": extract_razon_social(text)  # Se extrae desde la sección correcta
    }
    for field in ["Direccion_principal", "Email", "Telefono"]:
        results[field] = extract_field(text, FIELD_CONFIG[field])
    results.update(extract_names(text))
    results.update(extract_dept_city(text))
    return results

@app.post("/process-rut")
async def process_rut(file: UploadFile = File(...)):
    """ Procesa el archivo PDF y extrae los datos del RUT. """
    try:
        if not file.filename.lower().endswith('.pdf'):
            raise HTTPException(400, "Solo se permiten archivos PDF")
        contents = await file.read()
        text = extract_text(contents)
        data = smart_extractor(text, file.filename)
        return JSONResponse({"filename": file.filename, "data": data})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Error procesando el documento: {str(e)}"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
