"""Gemeinsame Standardwerte fuer den Bild-restaurieren-Modus.

Das Modul bleibt absichtlich frei von GUI-, Google- und Gemini-Abhaengigkeiten,
damit sowohl der Job-Assistent als auch die Generierungs-Pipeline denselben
Standardprompt verwenden koennen.
"""

DEFAULT_RESTORATION_PROMPT = """Faithfully restore and reconstruct the provided source image.

Preserve exactly:
- the complete original composition, framing and crop
- every object's position, shape, scale and proportions
- the camera angle, perspective and depth relationships
- the identity, pose and appearance of every person
- the original colors, lighting, background and visual style
- all intentional image content

Improve only:
- sharpness and clearly defined edges
- fine, natural-looking details and textures
- clean, precise lines
- local contrast and clarity
- compression artifacts, pixelation, blur and noise

Do not add, remove, move, replace, redesign or reinterpret anything.
Do not crop, extend, rotate or mirror the image.
Do not zoom into the subject. Keep every object fully visible with at least
the same breathing room to all image edges as in the source.
Do not invent text, logos, faces, objects or background details.
The result must look like a higher-quality reconstruction of the same image,
not like a new composition or a stylistic reinterpretation."""


ORIGINAL_STYLE_OPTION = "— Originalstil des Ausgangsbildes —"
