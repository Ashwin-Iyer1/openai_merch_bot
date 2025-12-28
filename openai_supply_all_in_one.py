"""
OpenAI Supply - All-in-One Script
Fetches data from supply.openai.com, parses buyable items, and generates HTML page
"""

import requests
import re
import json
import time
import os


def fetch_html(url):
    """Fetch HTML content from URL"""
    print(f"** Fetching HTML from {url}...")
    try:
        headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "accept-language": "en-US,en;q=0.9,es;q=0.8",
            "cache-control": "max-age=0",
            "priority": "u=0, i",
            "sec-ch-ua": "\"Google Chrome\";v=\"143\", \"Chromium\";v=\"143\", \"Not A(Brand\";v=\"24\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Windows\"",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        print(f"* Successfully fetched {len(response.text)} characters")
        return response.text
    except requests.exceptions.RequestException as e:
        print(f"* Error fetching URL: {e}")
        return None


def extract_buyable_products(html_content):
    """Extract all buyable products from the HTML - using escaped JSON data in Next.js hydration"""
    print("\n** Parsing HTML for buyable products (Next.js Data method)...")
    
    # The data is inside a JS string with escaped quotes \"
    # We look for the pattern: {\"id\":\"gid://shopify/Product/...
    # Note: We need to handle the escaping in the regex
    
    # Regex found 116 variants in tests. Use finditer to locate them.
    # Pattern matches start of object: {\"id\":\"gid://...\",\"handle\":\"...\"
    start_pattern = r'\{\\"id\\":\\"(gid://shopify/Product/[^\\\\]+)\\",\\"handle\\":\\"([^\\\\]+)\\"'
    
    product_matches = list(re.finditer(start_pattern, html_content))
    print(f"   Found {len(product_matches)} product starts")
    
    all_products = []
    seen_ids = set()
    
    for i, match in enumerate(product_matches):
        start_idx = match.start()
        
        # Determine end index: next product start or end of list
        if i + 1 < len(product_matches):
            end_idx = product_matches[i+1].start()
            # The next match starts with {"id"... 
            # The current object ends before that, usually with },
            # We want to grab up to the comma before the next object
            chunk = html_content[start_idx:end_idx]
            # Strip trailing comma and brace if needed, looks like: ...},"
            chunk = chunk.strip().rstrip(',').rstrip(',') # Remove trailing comma
        else:
            # Last item, goes until the end of the array
            # Look for closing bracket of the array ] or similar
            # Safeguard: take next 50000 chars
            chunk = html_content[start_idx:start_idx + 50000]
            # Truncate at first sequence that closes the array like "}]"
            # But the object ends with }
            # Let's find the closing sequence
            end_match = re.search(r'\}\](?:,|$)', chunk)
            if end_match:
                chunk = chunk[:end_match.start()+1]
        
        # Now chunk should be the full escaped JSON object: {"id":...,...}
        # But wait, finditer pattern matched the START. 
        # chunk includes the start.
        
        # Unescape
        clean_json = chunk.replace('\\"', '"').replace('\\\\', '\\')
        
        # Attempt to parse
        # If chunk is dirty (contains multiple objects or trailing text), trim it
        # We know it starts with {
        # We want to find the matching }
        
        try:
             # Fast path: try parsing directly (if logic above was perfect)
             data = json.loads(clean_json)
             process_data(data, all_products, seen_ids)
             continue
        except:
            pass
            
        # Slow path: find matching brace
        # Count braces
        brace_count = 0
        valid_json = ""
        for char in clean_json:
            valid_json += char
            if char == '{': brace_count += 1
            elif char == '}': brace_count -= 1
            
            if brace_count == 0 and valid_json.strip():
                try:
                    data = json.loads(valid_json)
                    process_data(data, all_products, seen_ids)
                    break
                except:
                    continue

    print(f"\n* Found {len(all_products)} verified buyable products")
    if not all_products:
        print("   ** No products passed the filter.")
        
    return all_products

def process_data(data, all_products, seen_ids):
    # FILTER LOGIC
    is_buyable = False
    
    # Check 1: Explicit 'buyable' flag
    ae = data.get('archiveEasteregg', {})
    if ae and ae.get('value') == 'buyable':
        is_buyable = True
        
    # Check 2: Handle fallback
    handle = data.get('handle', '')
    if 'buyable' in handle.lower():
        is_buyable = True
    
    # Check 3: Price > 0 implies buyable usually, but archive items often have price 0
    # "Thinking Deeply Cap" had price 0.0. "10 Years" had 50.0.
    # So Price > 0 is a good signal if tag is missing.
    price_val = 0.0
    try:
        price_str = data.get('priceRange', {}).get('minVariantPrice', {}).get('amount', '0.0')
        price_val = float(price_str)
        if price_val > 0:
             is_buyable = True
    except:
        pass
        
    if is_buyable:
        product_id = data.get('id')
        if product_id in seen_ids: return
        
        title = data.get('title', 'Unknown')
        image_url = ""
        images = data.get('images', [])
        if images: image_url = images[0].get('url', '')
        elif data.get('featuredImage'): image_url = data.get('featuredImage', {}).get('url', '')
        
        if '?' in image_url: image_url = image_url.split('?')[0]
        
        price = data.get('priceRange', {}).get('minVariantPrice', {}).get('amount', 'N/A')
        
        all_products.append({
            'id': product_id,
            'title': title,
            'handle': handle,
            'featuredImage': {'url': image_url},
            'priceRange': {'minVariantPrice': {'amount': price, 'currencyCode': 'USD'}},
            'availableForSale': data.get('availableForSale', False),
            'description': data.get('description', '')
        })
        seen_ids.add(product_id)
        print(f"   * {title} - ${price}")



def generate_html_page(products):
    """Generate the HTML page with buyable products"""
    print("\n** Generating HTML page...")
    
    # Sort products by title
    products.sort(key=lambda x: x.get('title', ''))
    
    # Convert products to JSON string for embedding
    products_json_str = json.dumps(products)
    
    # Generate product cards HTML
    product_cards_html = ""
    for product in products:
        product_id = product.get('id', '')
        title = product.get('title', 'Unknown')
        handle = product.get('handle', '')
        image_url = product.get('featuredImage', {}).get('url', '')
        
        # Get description
        description = product.get('archiveDescription', {}).get('value', '')
        if not description:
            description = product.get('description', '')
        description = description[:200]  # Limit length
        
        product_cards_html += f'''            <div class="product-card" data-product-id="{product_id}" onclick="showProductDetails('{product_id}')">
                <img src="{image_url}" alt="{title}" class="product-image" loading="lazy">
                <div class="product-info">
                    <h2 class="product-title">{title}</h2>
                    <div class="product-handle">{handle}</div>
                    <p class="product-description">{description}</p>
                    <span class="badge">Buyable</span>
                </div>
            </div>
'''
    
    # Complete HTML template
    html_content = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OpenAI Supply - Buyable Items</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: #f5f5f5;
            color: #1a1a1a;
            line-height: 1.6;
        }}
        
        .header {{
            background: #1a1a1a;
            color: white;
            padding: 2rem 1rem;
            text-align: center;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        
        .header h1 {{
            font-size: 2.5rem;
            font-weight: 700;
            margin-bottom: 0.5rem;
        }}
        
        .header p {{
            font-size: 1.1rem;
            opacity: 0.8;
        }}
        
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 2rem 1rem;
        }}
        
        .info-banner {{
            background: #10a37f;
            color: white;
            padding: 1rem;
            border-radius: 8px;
            margin-bottom: 2rem;
            text-align: center;
        }}
        
        .products-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 2rem;
            margin-top: 2rem;
        }}
        
        .product-card {{
            background: white;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            transition: transform 0.3s ease, box-shadow 0.3s ease;
            cursor: pointer;
        }}
        
        .product-card:hover {{
            transform: translateY(-8px);
            box-shadow: 0 12px 24px rgba(0,0,0,0.15);
        }}
        
        .product-image {{
            width: 100%;
            aspect-ratio: 1;
            object-fit: contain;
            background: #fafafa;
            padding: 1rem;
        }}
        
        .product-info {{
            padding: 1.5rem;
        }}
        
        .product-title {{
            font-size: 1.25rem;
            font-weight: 600;
            margin-bottom: 0.5rem;
            color: #1a1a1a;
        }}
        
        .product-handle {{
            font-size: 0.875rem;
            color: #666;
            font-family: 'Courier New', monospace;
            background: #f5f5f5;
            padding: 0.25rem 0.5rem;
            border-radius: 4px;
            display: inline-block;
            margin-bottom: 0.75rem;
        }}
        
        .product-description {{
            font-size: 0.95rem;
            color: #555;
            line-height: 1.5;
        }}
        
        .badge {{
            display: inline-block;
            background: #10a37f;
            color: white;
            padding: 0.25rem 0.75rem;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-top: 0.75rem;
        }}
        
        .footer {{
            text-align: center;
            padding: 2rem 1rem;
            color: #666;
            font-size: 0.9rem;
        }}
        
        /* Modal styles */
        .modal {{
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            overflow: auto;
            background-color: rgba(0,0,0,0.8);
            animation: fadeIn 0.3s;
        }}
        
        @keyframes fadeIn {{
            from {{ opacity: 0; }}
            to {{ opacity: 1; }}
        }}
        
        .modal-content {{
            background-color: white;
            margin: 2% auto;
            padding: 0;
            border-radius: 12px;
            width: 90%;
            max-width: 800px;
            max-height: 90vh;
            overflow-y: auto;
            animation: slideIn 0.3s;
        }}
        
        @keyframes slideIn {{
            from {{
                transform: translateY(-50px);
                opacity: 0;
            }}
            to {{
                transform: translateY(0);
                opacity: 1;
            }}
        }}
        
        .modal-header {{
            background: #1a1a1a;
            color: white;
            padding: 1.5rem;
            border-radius: 12px 12px 0 0;
            position: relative;
        }}
        
        .modal-header h2 {{
            margin: 0;
            font-size: 1.75rem;
        }}
        
        .close {{
            position: absolute;
            right: 1.5rem;
            top: 1.5rem;
            color: white;
            font-size: 2rem;
            font-weight: bold;
            cursor: pointer;
            transition: opacity 0.3s;
        }}
        
        .close:hover {{
            opacity: 0.7;
        }}
        
        .modal-body {{
            padding: 2rem;
        }}
        
        .loading {{
            text-align: center;
            padding: 3rem;
            color: #666;
        }}
        
        .spinner {{
            border: 3px solid #f3f3f3;
            border-top: 3px solid #10a37f;
            border-radius: 50%;
            width: 40px;
            height: 40px;
            animation: spin 1s linear infinite;
            margin: 0 auto 1rem;
        }}
        
        @keyframes spin {{
            0% {{ transform: rotate(0deg); }}
            100% {{ transform: rotate(360deg); }}
        }}
        
        .product-details {{
            display: grid;
            gap: 1rem;
        }}
        
        .detail-row {{
            padding: 0.75rem;
            background: #f9f9f9;
            border-radius: 6px;
        }}
        
        .detail-label {{
            font-weight: 600;
            color: #666;
            font-size: 0.875rem;
            margin-bottom: 0.25rem;
        }}
        
        .detail-value {{
            color: #1a1a1a;
            font-size: 1rem;
        }}
        
        .product-images {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
            gap: 1rem;
            margin-top: 1rem;
        }}
        
        .product-images img {{
            width: 100%;
            border-radius: 8px;
            border: 2px solid #e0e0e0;
        }}
        
        .variants-list {{
            display: grid;
            gap: 0.5rem;
            margin-top: 0.5rem;
        }}
        
        .variant-item {{
            padding: 0.5rem 1rem;
            background: white;
            border: 1px solid #e0e0e0;
            border-radius: 6px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        
        @media (max-width: 768px) {{
            .products-grid {{
                grid-template-columns: 1fr;
            }}
            
            .header h1 {{
                font-size: 1.75rem;
            }}
            
            .modal-content {{
                width: 95%;
                margin: 5% auto;
            }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>** OpenAI Supply Co.</h1>
        <p>Purchaseable Items - December 2025</p>
    </div>
    
    <div class="container">
        <div class="info-banner">
            <strong>* {len(products)} Buyable Items Found</strong> - Click any item to see detailed information!
        </div>
        
        <div class="products-grid">
{product_cards_html}        </div>
    </div>
    
    <!-- Modal for Product Details -->
    <div id="productModal" class="modal">
        <div class="modal-content">
            <div class="modal-header">
                <span class="close" onclick="closeModal()">&times;</span>
                <h2 id="modalTitle">Product Details</h2>
            </div>
            <div class="modal-body" id="modalBody">
                <div class="loading">
                    <div class="spinner"></div>
                    <p>Loading product details...</p>
                </div>
            </div>
        </div>
    </div>
    
    <div class="footer">
        <p>Data extracted from supply.openai.com on December 27, 2025</p>
        <p style="margin-top: 0.5rem;">Items marked as "buyable" have special handles and can be purchased</p>
    </div>
    
    <script>
        // Embedded product data
        const productsData = {products_json_str};
        
        function showProductDetails(productId) {{
            const modal = document.getElementById('productModal');
            const modalTitle = document.getElementById('modalTitle');
            const modalBody = document.getElementById('modalBody');
            
            // Show modal with loading state
            modal.style.display = 'block';
            modalBody.innerHTML = `
                <div class="loading">
                    <div class="spinner"></div>
                    <p>Loading product details...</p>
                </div>
            `;
            
            // Find product in embedded data
            const productInfo = productsData.find(p => p.id === productId);
            
            if (productInfo) {{
                displayEmbeddedProductDetails(productInfo);
            }} else {{
                modalBody.innerHTML = '<div class="loading"><p>Product not found.</p></div>';
            }}
        }}
        
        function displayEmbeddedProductDetails(product) {{
            const modalTitle = document.getElementById('modalTitle');
            const modalBody = document.getElementById('modalBody');
            
            modalTitle.textContent = product.title || 'Product Details';
            
            // Build comprehensive product details HTML
            let html = '<div class="product-details">';
            
            // Product Title
            html += `
                <div class="detail-row">
                    <div class="detail-label">Product Name</div>
                    <div class="detail-value"><strong style="font-size: 1.2rem;">${{product.title || 'N/A'}}</strong></div>
                </div>
            `;
            
            // Product ID
            html += `
                <div class="detail-row">
                    <div class="detail-label">Product ID</div>
                    <div class="detail-value" style="font-family: monospace; font-size: 0.85rem;">${{product.id || 'N/A'}}</div>
                </div>
            `;
            
            // Handle
            html += `
                <div class="detail-row">
                    <div class="detail-label">Handle (URL Slug)</div>
                    <div class="detail-value" style="font-family: monospace;">${{product.handle || 'N/A'}}</div>
                </div>
            `;
            
            // Product Type
            if (product.productType) {{
                html += `
                    <div class="detail-row">
                        <div class="detail-label">Product Type</div>
                        <div class="detail-value">${{product.productType}}</div>
                    </div>
                `;
            }}
            
            // Availability
            html += `
                <div class="detail-row">
                    <div class="detail-label">Available for Sale</div>
                    <div class="detail-value" style="color: ${{product.availableForSale ? '#10a37f' : '#d32f2f'}}; font-weight: 600;">
                        ${{product.availableForSale ? '✓ Yes' : '✗ No'}}
                    </div>
                </div>
            `;
            
            // Price Range
            if (product.priceRange) {{
                const minPrice = product.priceRange.minVariantPrice?.amount || 'N/A';
                const maxPrice = product.priceRange.maxVariantPrice?.amount || minPrice;
                const currency = product.priceRange.minVariantPrice?.currencyCode || 'USD';
                
                html += `
                    <div class="detail-row">
                        <div class="detail-label">Price Range</div>
                        <div class="detail-value" style="font-size: 1.3rem; font-weight: 700; color: #10a37f;">
                            $${{minPrice}} ${{minPrice !== maxPrice ? '- $$' + maxPrice : ''}} ${{currency}}
                        </div>
                    </div>
                `;
            }}
            
            // Description
            if (product.archiveDescription && product.archiveDescription.value) {{
                html += `
                    <div class="detail-row">
                        <div class="detail-label">Description</div>
                        <div class="detail-value" style="line-height: 1.6;">${{product.archiveDescription.value}}</div>
                    </div>
                `;
            }} else if (product.description) {{
                html += `
                    <div class="detail-row">
                        <div class="detail-label">Description</div>
                        <div class="detail-value" style="line-height: 1.6;">${{product.description}}</div>
                    </div>
                `;
            }}
            
            // Options (Sizes, Colors, etc.)
            if (product.options && product.options.length > 0) {{
                product.options.forEach(option => {{
                    if (option.values && option.values.length > 0) {{
                        html += `
                            <div class="detail-row">
                                <div class="detail-label">${{option.name}} Options</div>
                                <div class="detail-value">
                                    <div style="display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.5rem;">
                        `;
                        
                        option.values.forEach(value => {{
                            html += `<span style="background: #e0e0e0; padding: 0.35rem 0.75rem; border-radius: 6px; font-size: 0.9rem; font-weight: 500;">${{value}}</span>`;
                        }});
                        
                        html += `
                                    </div>
                                </div>
                            </div>
                        `;
                    }}
                }});
            }}
            
            // Variants with detailed info - handle both formats (with/without edges)
            let variantsList = [];
            if (product.variants) {{
                if (product.variants.edges && product.variants.edges.length > 0) {{
                    // GraphQL format with edges
                    variantsList = product.variants.edges.map(edge => edge.node);
                }} else if (Array.isArray(product.variants)) {{
                    // Direct array format
                    variantsList = product.variants;
                }}
            }}
            
            if (variantsList.length > 0) {{
                html += `
                    <div class="detail-row">
                        <div class="detail-label">Variants (${{variantsList.length}} available)</div>
                        <div class="variants-list">
                `;
                
                variantsList.forEach(variant => {{
                    const price = variant.price?.amount || 'N/A';
                    const currency = variant.price?.currencyCode || 'USD';
                    const availability = variant.availableForSale;
                    const quantity = variant.quantityAvailable !== undefined ? variant.quantityAvailable : '?';
                    
                    html += `
                        <div class="variant-item">
                            <div>
                                <strong>${{variant.title || 'Default'}}</strong>
                            </div>
                            <div style="text-align: right;">
                                <div style="font-weight: 700; font-size: 1.1rem; color: #10a37f;">$${{price}} ${{currency}}</div>
                                <div style="font-size: 0.85rem; color: ${{availability ? '#10a37f' : '#d32f2f'}}; margin-top: 0.25rem;">
                                    ${{availability ? '✓ Available' : '✗ Unavailable'}}
                                </div>
                                ${{quantity !== '?' ? '<div style="font-size: 0.8rem; color: #666; margin-top: 0.25rem;">Qty: ' + quantity + '</div>' : ''}}
                            </div>
                        </div>
                    `;
                }});
                
                html += `
                        </div>
                    </div>
                `;
            }}
            
            // Images - handle both formats (with/without edges)
            const allImages = [];
            if (product.featuredImage && product.featuredImage.url) {{
                allImages.push(product.featuredImage);
            }}
            if (product.images) {{
                if (product.images.edges) {{
                    // GraphQL format with edges
                    product.images.edges.forEach(edge => {{
                        const img = edge.node;
                        if (!allImages.some(i => i.url === img.url)) {{
                            allImages.push(img);
                        }}
                    }});
                }} else if (Array.isArray(product.images)) {{
                    // Direct array format
                    product.images.forEach(img => {{
                        if (!allImages.some(i => i.url === img.url)) {{
                            allImages.push(img);
                        }}
                    }});
                }}
            }}
            
            if (allImages.length > 0) {{
                html += `
                    <div class="detail-row">
                        <div class="detail-label">Product Images (${{allImages.length}})</div>
                        <div class="product-images">
                `;
                
                allImages.forEach(img => {{
                    html += `
                        <div style="position: relative;">
                            <img src="${{img.url}}" alt="${{img.altText || product.title}}" loading="lazy" 
                                 style="cursor: pointer;" 
                                 onclick="window.open('${{img.url}}', '_blank')">
                        </div>
                    `;
                }});
                
                html += `
                        </div>
                    </div>
                `;
            }}
            
            // Product URL
            html += `
                <div class="detail-row" style="background: #f0f9ff; border: 2px solid #10a37f;">
                    <div class="detail-label" style="color: #10a37f;">*� Product Page</div>
                    <div class="detail-value">
                        <a href="https://supply.openai.com/products/${{product.handle}}" 
                           target="_blank" 
                           style="color: #10a37f; text-decoration: none; font-weight: 700; font-size: 1.05rem; display: inline-flex; align-items: center; gap: 0.5rem;">
                            View on OpenAI Supply →
                        </a>
                    </div>
                </div>
            `;
            
            html += '</div>';
            
            modalBody.innerHTML = html;
        }}
        
        function closeModal() {{
            const modal = document.getElementById('productModal');
            modal.style.display = 'none';
        }}
        
        // Close modal when clicking outside of it
        window.onclick = function(event) {{
            const modal = document.getElementById('productModal');
            if (event.target === modal) {{
                closeModal();
            }}
        }}
        
        // Close modal with Escape key
        document.addEventListener('keydown', function(event) {{
            if (event.key === 'Escape') {{
                closeModal();
            }}
        }});
    </script>
</body>
</html>'''
    
    return html_content


import datetime

def load_env_file(filepath):
    """Simple wrapper to load env vars from file"""
    try:
        with open(filepath, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'): continue
                if '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()
        print(f"Loaded environment from {filepath}")
    except Exception as e:
        print(f"Error loading env file: {e}")

def send_telegram_notification(message, token, chat_id):
    """Send message via Telegram"""
    if not token or not chat_id:
        print("Telegram credentials missing, skipping notification")
        return
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        print(f"Telegram notification sent: {message[:50]}...")
    except Exception as e:
        print(f"Failed to send Telegram notification: {e}")

def main():
    """Main function to orchestrate the entire process"""
    print("=" * 80)
    print("OpenAI Supply - All-in-One Script (Monitor Mode)")
    print("=" * 80)
    
    # Load Environment Variables
    env_path = r'./.env'
    if os.path.exists(env_path):
        load_env_file(env_path)
    else:
        print(f"Warning: .env not found at {env_path}")

    # Setup Telegram
    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
    TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
    
    if os.environ.get('DEV_MODE', '').lower() == 'true':
        print("DEV MODE: Using Dev Telegram credentials")
        TELEGRAM_BOT_TOKEN = os.environ.get('DEV_TELEGRAM_BOT_TOKEN', TELEGRAM_BOT_TOKEN)
        TELEGRAM_CHAT_ID = os.environ.get('DEV_TELEGRAM_CHAT_ID', TELEGRAM_CHAT_ID)

    if TELEGRAM_BOT_TOKEN:
        print(f"Telegram Configured (Chat ID: {TELEGRAM_CHAT_ID})")
        # Send startup message
        send_telegram_notification("🤖 OpenAI Supply Monitor Started", TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    else:
        print("Integration Warning: Telegram token not found")

    
    known_products = {} # id -> available status
    first_run = True
    iteration = 0
    
    try:
        while True:
            iteration += 1
            timestamp = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"\n[{timestamp}] Checking Iteration #{iteration}...")
            
            # Step 1: Fetch HTML from website
            url = "https://supply.openai.com/"
            html_content = fetch_html(url)
            
            if not html_content:
                print("Skipping this iteration due to fetch error.")
                time.sleep(10)
                continue
            
            # Step 2: Extract buyable products
            buyable_products = extract_buyable_products(html_content)
            
            # Step 3: Check for changes
            current_ids = set()
            newly_available = []
            
            for product in buyable_products:
                print(product)
                p_id = product.get('id')
                p_title = product.get('title')
                p_available = product.get('availableForSale', False)
                p_handle = product.get('handle')
                p_price = product.get('priceRange', {}).get('minVariantPrice', {}).get('amount', 'N/A')
                
                current_ids.add(p_id)
                
                # Logic to detect change:
                # 1. New product we haven't seen before AND it's available
                # 2. Existing product that WAS NOT available and NOW IS available
                
                prev_available = known_products.get(p_id)
                
                if prev_available is None:
                    # New product found
                    known_products[p_id] = p_available
                    if p_available:
                        # New AND Available
                        if not first_run: # Don't spam on first run unless you want a status report
                             newly_available.append(product)
                    else:
                        print(f"   Tracking new item: {p_title} (Currently Unavailable)")

                elif p_available and not prev_available:
                    # Status changed to Available!
                    print(f"   !!! BECAME AVAILABLE: {p_title}")
                    newly_available.append(product)
                    known_products[p_id] = True # Update status
                
                elif not p_available and prev_available:
                     # Status changed to Unavailable
                     print(f"   Became unavailable: {p_title}")
                     known_products[p_id] = False
                     
            
            # Send Notifications
            if newly_available:
                print(f"** SENDING ALERTS FOR {len(newly_available)} ITEMS **")
                for p in newly_available:
                    title = p.get('title')
                    price = p.get('priceRange', {}).get('minVariantPrice', {}).get('amount')
                    handle = p.get('handle')
                    url = f"https://supply.openai.com/products/{handle}"
                    
                    msg = (
                        f"🚨 <b>ITEM AVAILABLE!</b>\n\n"
                        f"<b>{title}</b>\n"
                        f"Price: ${price}\n"
                        f"<a href='{url}'>Product Link</a>"
                    )
                    send_telegram_notification(msg, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

            # Update known_products for any others (e.g. if something was removed completely)
            # (Optional: Clean up known_products if items are removed from site)
            
            if first_run:
                print(f"Initial scan complete. Tracking {len(known_products)} items.")
                first_run = False
            
            # Step 4: Generate HTML page (still useful to have latest view)
            html_page = generate_html_page(buyable_products)
            with open('buyable_items.html', 'w', encoding='utf-8') as f:
                f.write(html_page)
            print("Refreshed buyable_items.html")

            # Wait
            sleep_sec = 20 # Human-like polling
            print(f"Sleeping {sleep_sec}s...")
            time.sleep(sleep_sec)
            
    except KeyboardInterrupt:
        print("\nStopped by user.")
    except Exception as e:
        print(f"\nCRITICAL ERROR: {e}")
        # Try to alert user of crash
        if TELEGRAM_BOT_TOKEN:
            send_telegram_notification(f"⚠️ Script Crashed: {e}", TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

if __name__ == "__main__":
    main()

