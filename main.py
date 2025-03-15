# Generate QR Code
shareable_url = f"https://clinic-management-system-27d11.web.app/dashboard?publicToken={public_token}&sessionToken={session_token}"
qr = qrcode.make(shareable_url)

# Resize QR code to 2x2 inches (600x600 pixels at 300 DPI)
qr = qr.resize((600, 600), Image.Resampling.LANCZOS)

# Create a new image with space for text (e.g., 100px extra height)
text_height = 100
final_image = Image.new("RGB", (600, 700), "white")  # Extra 100 pixels height for text
final_image.paste(qr, (0, 0))  # Place QR at the top

# Add text below the QR code
draw = ImageDraw.Draw(final_image)

# Load font (adjust font size as needed)
try:
    font = ImageFont.truetype("arial.ttf", 40)  # Try Arial font (Windows)
except IOError:
    font = ImageFont.load_default()  # Fallback for systems without Arial

# Define text and position
text = "Scan this for live updates"
text_width, text_height = draw.textsize(text, font=font)
text_x = (600 - text_width) // 2  # Center text
text_y = 620  # Position below QR code

# Draw text on the image
draw.text((text_x, text_y), text, fill="black", font=font)

# Save to a bytes buffer
img_io = io.BytesIO()
final_image.save(img_io, format="PNG")
img_io.seek(0)

# Now `img_io` contains the QR code image with text
