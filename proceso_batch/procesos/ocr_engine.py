import cv2
import numpy as np
import easyocr

# ============================================================
# Módulo OCR Local para DocUNI v3.0
# Implementa el submódulo óptico: OpenCV + EasyOCR
# ============================================================

# Instanciación única global del lector OCR (español)
# Se recomienda activar gpu=True si hay hardware Nvidia disponible
reader = easyocr.Reader(['es'], gpu=False)


def preprocesar_imagen(imagen_bytes: bytes) -> np.ndarray:
    """
    Preprocesamiento óptico con OpenCV.
    Convierte el buffer de bytes a escala de grises y aplica
    un desenfoque mediano para optimizar los bordes de caracteres.
    """
    nparr = np.frombuffer(imagen_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)

    if img is None:
        return None

    # Reducción de ruido mediante filtro de mediana
    img_limpia = cv2.medianBlur(img, 3)
    return img_limpia


def preprocesar_imagen_pil(pil_image) -> np.ndarray:
    """
    Preprocesamiento óptico desde un objeto PIL.Image (usado por pdf2image).
    Convierte a escala de grises y aplica filtro de mediana.
    """
    # Convertir PIL Image a array numpy
    img_array = np.array(pil_image)

    # Convertir a escala de grises si es necesario
    if len(img_array.shape) == 3:
        img = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
    else:
        img = img_array

    # Reducción de ruido mediante filtro de mediana
    img_limpia = cv2.medianBlur(img, 3)
    return img_limpia


def extraer_texto_ocr(imagen_procesada: np.ndarray) -> str:
    """
    Inferencia de redes neuronales con EasyOCR.
    Extrae el texto crudo consolidado de una imagen preprocesada.
    """
    if imagen_procesada is None:
        return ""

    lineas_texto = reader.readtext(imagen_procesada, detail=0)
    return "\n".join(lineas_texto)
