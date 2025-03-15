@app.get("/generate-qr/{public_token}/{session_token}")
def generate_qr(public_token: str, session_token: str, db: Session = Depends(get_db)):
    print(f"Received request with public_token: {public_token}, session_token: {session_token}")

    # Validate session token
    session = db.query(SessionModel).filter(SessionModel.session_token == session_token).first()
    if not session:
        print("Error: Invalid session token")
        return {"error": "Invalid session token"}
    
    print("Session token validated successfully.")
    
    # Generate shareable URL
    shareable_url = f"https://clinic-management-system-27d11.web.app/dashboard?publicToken={public_token}&sessionToken={session_token}"
    print(f"Generated shareable URL: {shareable_url}")
    
    # Generate QR Code
    try:
        qr = qrcode.make(shareable_url)
        print("QR Code generated successfully.")
    except Exception as e:
        print(f"Error generating QR Code: {e}")
        return {"error": "Failed to generate QR code"}
    
    # Resize QR code to 4x4 inches at 300 DPI (i.e., 1200x1200 pixels)
    try:
        qr = qr.resize((1200, 1200), Image.Resampling.LANCZOS)
        print("QR Code resized to 1200x1200 pixels successfully.")
    except Exception as e:
        print(f"Error resizing QR Code: {e}")
        return {"error": "Failed to resize QR code"}
    
    # Create a new image with extra space for text (150 pixels extra height)
    final_height = 1200 + 150  # 1200 for QR + 150 for text
    final_image = Image.new("RGB", (1200, final_height), "white")
    final_image.paste(qr, (0, 0))
    print("QR Code pasted onto final image.")
    
    # Add text below the QR code
    draw = ImageDraw.Draw(final_image)
    
    # Try to load a robust TrueType font. Adjust the path if necessary.
    try:
        # Attempt to load Arial
        font = ImageFont.truetype("arial.ttf", 80)
        print("Loaded Arial font successfully at size 80.")
    except IOError:
        try:
            # Fallback to DejaVuSans-Bold, which is often available on Linux systems
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", 80)
            print("Loaded DejaVuSans-Bold font successfully at size 80.")
        except IOError:
            font = ImageFont.load_default()
            print("Failed to load TrueType fonts. Using default font (this may be small).")
    
    text = "Scan for Live Updates"
    # Calculate text dimensions using textbbox
    text_bbox = draw.textbbox((0, 0), text, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height_calculated = text_bbox[3] - text_bbox[1]
    print(f"Text dimensions calculated: width={text_width}, height={text_height_calculated}")
    
    # Center text horizontally, and vertically center in the extra space
    text_x = (1200 - text_width) // 2
    text_y = 1200 + ((150 - text_height_calculated) // 2)
    
    try:
        draw.text((text_x, text_y), text, fill="black", font=font)
        print("Text added to the image successfully.")
    except Exception as e:
        print(f"Error adding text: {e}")
        return {"error": "Failed to add text to image"}
    
    # Save the final image to a bytes buffer
    try:
        img_io = io.BytesIO()
        final_image.save(img_io, format="PNG")
        img_io.seek(0)
        print("Final image saved to bytes buffer successfully.")
    except Exception as e:
        print(f"Error saving image: {e}")
        return {"error": "Failed to save final image"}
    
    print("Returning the QR code image as a response.")
    return StreamingResponse(img_io, media_type="image/png")
