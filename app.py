from flask import Flask, render_template, jsonify, request, send_file
import json
import struct
import threading
import bluetooth
import socket
from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__)

# in memory-color storage
current_color = {"r": 0, "g": 0, "b": 0}
palette = []

# COLOR MATH FUNCTIONS


def rgb_to_hex(r, g, b):
    return f"{r:02X}{g:02X}{b:02X}"

def hex_to_rgb(hex_str):
    """Convert a hex string like '#FF6347' or 'FF6347' to (r, g, b) ints."""
    hex_str = hex_str.lstrip('#')
    if len(hex_str) != 6:
        raise ValueError("Hex must be 6 characters")
    r = int(hex_str[0:2], 16)
    g = int(hex_str[2:4], 16)
    b = int(hex_str[4:6], 16)
    return r, g, b

def rgb_to_cmyk(r, g, b):
    if (r, g, b) == (0, 0, 0):
        return 0, 0, 0, 100
    r_, g_, b_ = r / 255, g / 255, b / 255
    k = 1 - max(r_, g_, b_)
    c = (1 - r_ - k) / (1 - k)
    m = (1 - g_ - k) / (1 - k)
    y = (1 - b_ - k) / (1 - k)
    return round(c * 100), round(m * 100), round(y * 100), round(k * 100)

def rgb_to_hsl(r, g, b):
    r_, g_, b_ = r / 255, g / 255, b / 255
    cmax = max(r_, g_, b_)
    cmin = min(r_, g_, b_)
    delta = cmax - cmin

    l = (cmax + cmin) / 2
    s = 0 if delta == 0 else delta / (1 - abs(2 * l - 1))

    if delta == 0:
        h = 0
    elif cmax == r_:
        h = 60 * (((g_ - b_) / delta) % 6)
    elif cmax == g_:
        h = 60 * (((b_ - r_) / delta) + 2)
    else:
        h = 60 * (((r_ - g_) / delta) + 4)

    return h, s, l

def hsl_to_rgb(h, s, l):
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = l - c / 2

    if 0 <= h < 60:     r_, g_, b_ = c, x, 0
    elif 60 <= h < 120: r_, g_, b_ = x, c, 0
    elif 120 <= h < 180: r_, g_, b_ = 0, c, x
    elif 180 <= h < 240: r_, g_, b_ = 0, x, c
    elif 240 <= h < 300: r_, g_, b_ = x, 0, c
    else:               r_, g_, b_ = c, 0, x

    return round((r_ + m) * 255), round((g_ + m) * 255), round((b_ + m) * 255)

def get_analogous(r, g, b):
    h, s, l = rgb_to_hsl(r, g, b)
    analogous_1 = hsl_to_rgb((h + 30) % 360, s, l)
    analogous_2 = hsl_to_rgb((h - 30) % 360, s, l)
    return [analogous_1, analogous_2]

def get_complementary(r, g, b):
    h, s, l = rgb_to_hsl(r, g, b)
    return hsl_to_rgb((h + 180) % 360, s, l)

def get_closest_color_name(r, g, b):
    """Find the closest named color from colorname.json using Euclidean distance."""
    try:
        with open('colorname.json', 'r') as file:
            color_list = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return "unknown"

    closest_name = "unknown"
    min_distance = float("inf")

    for item in color_list:
        try:
            hex_str = item['hex'].lstrip('#')
            cr = int(hex_str[0:2], 16)
            cg = int(hex_str[2:4], 16)
            cb = int(hex_str[4:6], 16)
        except (KeyError, ValueError):
            continue

        distance = ((r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2) ** 0.5
        if distance < min_distance:
            min_distance = distance
            closest_name = item['name']

    return closest_name

# def get_local_ip():
    # """Find the Pi 4's IP address on the local network."""
    # try:
        # s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # s.connect(("8.8.8.8", 80))
        # ip = s.getsockname()[0]
        # s.close()
        # return ip
    # except Exception:
        # return "127.0.0.1"

def get_local_ip():
    """Find the Pi 4's IP address on the local network.
    Prefers private/local-network IPs over public ones."""
    try:
        import subprocess
        result = subprocess.check_output(['hostname', '-I']).decode().strip()
        ips = result.split()

        # Prefer 10.x, 172.16-31.x, or 192.168.x (private network ranges)
        for ip in ips:
            if ip.startswith('10.') or ip.startswith('192.168.') or ip.startswith('172.'):
                return ip

        # Fall back to first IP if no private one found
        if ips:
            return ips[0]
    except Exception:
        pass

    return "127.0.0.1"


def build_color_data(r, g, b):
    hex_code = rgb_to_hex(r, g, b)
    c, m, y, k = rgb_to_cmyk(r, g, b)
    comp = get_complementary(r, g, b)
    analogous = get_analogous(r, g, b)
    return {
        "r": r, "g": g, "b": b,
        "hex": hex_code,
        "cmyk": {"c": c, "m": m, "y": y, "k": k},
        "complementary": {"r": comp[0], "g": comp[1], "b": comp[2]},
        "analogous": [
            {"r": a[0], "g": a[1], "b": a[2]} for a in analogous
        ],
        "name": get_closest_color_name(r, g, b),
    }

# JPG PALETTE EXPORT

def generate_palette_jpg(output_path):
    """Generate a JPG image of the current palette as a labeled grid of swatches."""
    if not palette:
        raise ValueError("Palette is empty")

    swatch_size = 200
    label_height = 60
    cols = 4
    padding = 20
    title_height = 80

    rows = (len(palette) + cols - 1) // cols

    width = cols * swatch_size + (cols + 1) * padding
    height = title_height + rows * (swatch_size + label_height) + (rows + 1) * padding

    img = Image.new("RGB", (width, height), color=(245, 245, 245))
    draw = ImageDraw.Draw(img)

    try:
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        hex_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        name_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    except (OSError, IOError):
        title_font = ImageFont.load_default()
        hex_font = ImageFont.load_default()
        name_font = ImageFont.load_default()

    title = "ColorPen Palette"
    bbox = draw.textbbox((0, 0), title, font=title_font)
    title_width = bbox[2] - bbox[0]
    draw.text(((width - title_width) // 2, 25), title, fill=(40, 40, 40), font=title_font)

    for i, color in enumerate(palette):
        col = i % cols
        row = i // cols

        x = padding + col * (swatch_size + padding)
        y = title_height + padding + row * (swatch_size + label_height + padding)

        rgb = (color["r"], color["g"], color["b"])
        draw.rectangle([x, y, x + swatch_size, y + swatch_size], fill=rgb)

        brightness = (rgb[0] * 299 + rgb[1] * 587 + rgb[2] * 114) / 1000
        text_color = (255, 255, 255) if brightness < 128 else (0, 0, 0)

        hex_text = f"#{color['hex']}"
        bbox = draw.textbbox((0, 0), hex_text, font=hex_font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        draw.text(
            (x + (swatch_size - text_width) // 2, y + (swatch_size - text_height) // 2),
            hex_text, fill=text_color, font=hex_font
        )

        name = color.get("name", "unknown")
        bbox = draw.textbbox((0, 0), name, font=name_font)
        name_width = bbox[2] - bbox[0]
        draw.text(
            (x + (swatch_size - name_width) // 2, y + swatch_size + 8),
            name, fill=(60, 60, 60), font=name_font
        )

    img.save(output_path, "JPEG", quality=95)


# ASE PALETTE EXPORT (Adobe Swatch Exchange)

def generate_palette_ase(output_path):
    """Write an Adobe ASE file from the current palette using the documented binary format."""
    if not palette:
        raise ValueError("Palette is empty")

    with open(output_path, 'wb') as f:
        # ASE header
        f.write(b'ASEF')
        f.write(struct.pack('>HH', 1, 0))
        f.write(struct.pack('>I', len(palette)))

        for color in palette:
            name = color.get("name", "Color")
            name_utf16 = name.encode('utf-16-be') + b'\x00\x00'
            name_length = len(name_utf16) // 2

            block_data = struct.pack('>H', name_length)
            block_data += name_utf16
            block_data += b'RGB '
            block_data += struct.pack('>fff',
                                      color["r"] / 255.0,
                                      color["g"] / 255.0,
                                      color["b"] / 255.0)
            block_data += struct.pack('>H', 2)

            f.write(struct.pack('>H', 0x0001))
            f.write(struct.pack('>I', len(block_data)))
            f.write(block_data)


# BLUETOOTH SERVER (background thread)

def bluetooth_server():
    global current_color

    server_sock = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
    server_sock.bind(("", 1))
    server_sock.listen(1)

    port = server_sock.getsockname()[1]
    print(f"Bluetooth server waiting on RFCOMM channel {port}...")

    while True:
        try:
            client_sock, client_info = server_sock.accept()
            print(f"Bluetooth connected to {client_info}")

            while True:
                data = client_sock.recv(1024)
                if not data:
                    break
                color = json.loads(data.decode("utf-8"))
                current_color = {"r": color["r"], "g": color["g"], "b": color["b"]}
                print(f"Received color: {current_color}")

        except OSError:
            print("Bluetooth connection lost, waiting for reconnect...")
            continue

# FLASK ROUTES

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/color")
def get_color():
    r, g, b = current_color["r"], current_color["g"], current_color["b"]
    return jsonify(build_color_data(r, g, b))

@app.route("/update_color", methods=["POST"])
def update_color():
    global current_color
    data = request.get_json()
    current_color = {"r": data["r"], "g": data["g"], "b": data["b"]}
    return jsonify({"status": "ok"})

@app.route("/save_color", methods=["POST"])
def save_color():
    r, g, b = current_color["r"], current_color["g"], current_color["b"]
    color_data = build_color_data(r, g, b)
    palette.append(color_data)
    return jsonify({"status": "ok", "palette": palette})

@app.route("/save_related/<which>", methods=["POST"])
def save_related(which):
    """Save the complementary or an analogous color to the palette."""
    r, g, b = current_color["r"], current_color["g"], current_color["b"]
    main_data = build_color_data(r, g, b)

    if which == "complementary":
        c = main_data["complementary"]
    elif which == "analogous_1":
        c = main_data["analogous"][0]
    elif which == "analogous_2":
        c = main_data["analogous"][1]
    else:
        return jsonify({"status": "error", "message": "Invalid color type"}), 400

    related_color_data = build_color_data(c["r"], c["g"], c["b"])
    palette.append(related_color_data)
    return jsonify({"status": "ok", "palette": palette})

@app.route("/save_custom", methods=["POST"])
def save_custom():
    """Add a user-picked color (from a color picker) to the palette."""
    data = request.get_json()
    hex_str = data.get("hex", "")

    try:
        r, g, b = hex_to_rgb(hex_str)
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid hex"}), 400

    color_data = build_color_data(r, g, b)
    palette.append(color_data)
    return jsonify({"status": "ok", "palette": palette})

@app.route("/edit_color/<int:index>", methods=["POST"])
def edit_color(index):
    """Replace the color at the given palette index with a user-picked color."""
    if not (0 <= index < len(palette)):
        return jsonify({"status": "error", "message": "Invalid index"}), 400

    data = request.get_json()
    hex_str = data.get("hex", "")

    try:
        r, g, b = hex_to_rgb(hex_str)
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid hex"}), 400

    palette[index] = build_color_data(r, g, b)
    return jsonify({"status": "ok", "palette": palette})

@app.route("/palette")
def get_palette():
    return jsonify(palette)

@app.route("/delete_color/<int:index>", methods=["POST"])
def delete_color(index):
    """Remove a single color from the palette by its index."""
    if 0 <= index < len(palette):
        palette.pop(index)
        return jsonify({"status": "ok", "palette": palette})
    return jsonify({"status": "error", "message": "Invalid index"}), 400

@app.route("/clear_palette", methods=["POST"])
def clear_palette():
    palette.clear()
    return jsonify({"status": "ok"})

@app.route("/export/jpg")
def export_jpg():
    """Export the palette as a JPG image."""
    if not palette:
        return jsonify({"status": "error", "message": "Palette is empty"}), 400

    try:
        output_path = "my_palette.jpg"
        generate_palette_jpg(output_path)

        return send_file(
            output_path,
            as_attachment=True,
            attachment_filename="colorpen_palette.jpg",
            mimetype="image/jpeg"
        )
    except Exception as e:
        print(f"JPG export failed: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/export/ase")
def export_ase():
    """Export the palette as an Adobe ASE file."""
    if not palette:
        return jsonify({"status": "error", "message": "Palette is empty"}), 400

    try:
        output_path = "my_palette.ase"
        generate_palette_ase(output_path)

        return send_file(
            output_path,
            as_attachment=True,
            attachment_filename="colorpen_palette.ase",
            mimetype="application/octet-stream"
        )
    except Exception as e:
        print(f"ASE export failed: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/server_url")
def server_url():
    """Return this server's URL so the browser can generate a QR code from it."""
    ip = get_local_ip()
    return jsonify({"url": f"http://{ip}:5000/"})

# STARTUP

if __name__ == "__main__":
    bt_thread = threading.Thread(target=bluetooth_server, daemon=True)
    bt_thread.start()
    app.run(host="0.0.0.0", port=5000, debug=False)
