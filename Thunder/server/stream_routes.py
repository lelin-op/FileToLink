# Thunder/server/stream_routes.py (addition - add this route before the final catch-all route)

# Add this import at the top with other imports
from Thunder.utils.database import db

# Add this route before the final @routes.get(r"/{path:.+}") catch-all route (around line 425)

@routes.get(r"/collection/{collection_id}", allow_head=True)
async def collection_preview(request: web.Request):
    """Display a collection of files with download/stream links for each."""
    try:
        collection_id = request.match_info["collection_id"].strip()
        
        # Validate collection_id format (UUID-like)
        if not collection_id or len(collection_id) < 4:
            raise FileNotFound("Invalid collection ID")
        
        # Fetch collection from database
        collection = await db.get_media_collection(collection_id)
        if not collection:
            raise FileNotFound("Collection not found")
        
        files = collection.get("files", [])
        if not files:
            raise FileNotFound("Collection has no files")
        
        # Render collection page
        rendered_page = await render_collection_page(collection)
        
        response = web.Response(
            text=rendered_page,
            content_type='text/html',
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Range, Content-Type, *",
                "X-Content-Type-Options": "nosniff",
            }
        )
        response.enable_compression()
        return response
    
    except FileNotFound as e:
        logger.debug(f"Collection error: {type(e).__name__} - {e}", exc_info=True)
        raise web.HTTPNotFound(text="Collection not found") from e
    except Exception as e:
        error_id = secrets.token_hex(6)
        logger.error(f"Collection error {error_id}: {e}", exc_info=True)
        raise web.HTTPInternalServerError(
            text=f"Server error occurred: {error_id}") from e


async def render_collection_page(collection: dict) -> str:
    """Render HTML page for displaying collection of files."""
    collection_id = collection.get("collection_id", "unknown")
    files = collection.get("files", [])
    total_size = collection.get("total_size", 0)
    file_count = collection.get("file_count", len(files))
    
    from Thunder.utils.human_readable import humanbytes
    
    # Build file items HTML
    file_items_html = ""
    for file_data in files:
        file_name = file_data.get("file_name", "Unknown File")
        file_size = file_data.get("file_size", 0)
        public_hash = file_data.get("public_hash", "")
        
        readable_size = humanbytes(file_size)
        encoded_name = quote_media_name(file_name)
        
        # Download and stream links
        download_link = f"{Var.URL.rstrip('/')}/f/{public_hash}/{encoded_name}"
        stream_link = f"{Var.URL.rstrip('/')}/watch/f/{public_hash}/{encoded_name}"
        
        file_items_html += f"""
        <div class="file-item">
            <div class="file-info">
                <h3 class="file-name">{file_name}</h3>
                <p class="file-size">📦 {readable_size}</p>
            </div>
            <div class="file-actions">
                <a href="{download_link}?disposition=attachment" class="btn btn-download" target="_blank">
                    📥 Download
                </a>
                <a href="{stream_link}" class="btn btn-stream" target="_blank">
                    ▶️ Stream
                </a>
            </div>
        </div>
        """
    
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>📦 File Collection</title>
        <style>
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }}
            
            .container {{
                background: white;
                border-radius: 12px;
                box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
                max-width: 800px;
                width: 100%;
                overflow: hidden;
            }}
            
            .header {{
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 40px 30px;
                text-align: center;
            }}
            
            .header h1 {{
                font-size: 2.5em;
                margin-bottom: 10px;
                font-weight: 700;
            }}
            
            .header .stats {{
                font-size: 1.1em;
                opacity: 0.9;
                margin-top: 15px;
            }}
            
            .stats-item {{
                display: inline-block;
                margin: 0 20px;
            }}
            
            .stats-item strong {{
                color: #fff;
            }}
            
            .content {{
                padding: 30px;
            }}
            
            .files-list {{
                display: flex;
                flex-direction: column;
                gap: 15px;
            }}
            
            .file-item {{
                border: 2px solid #f0f0f0;
                border-radius: 8px;
                padding: 20px;
                display: flex;
                justify-content: space-between;
                align-items: center;
                transition: all 0.3s ease;
                background: #fafafa;
            }}
            
            .file-item:hover {{
                border-color: #667eea;
                background: #f5f7ff;
                box-shadow: 0 4px 12px rgba(102, 126, 234, 0.1);
            }}
            
            .file-info {{
                flex: 1;
                min-width: 0;
            }}
            
            .file-name {{
                font-size: 1.1em;
                color: #333;
                word-break: break-word;
                margin-bottom: 8px;
                font-weight: 600;
            }}
            
            .file-size {{
                color: #666;
                font-size: 0.95em;
            }}
            
            .file-actions {{
                display: flex;
                gap: 10px;
                margin-left: 20px;
                flex-shrink: 0;
            }}
            
            .btn {{
                padding: 10px 20px;
                border-radius: 6px;
                text-decoration: none;
                font-weight: 600;
                font-size: 0.95em;
                transition: all 0.3s ease;
                white-space: nowrap;
                border: none;
                cursor: pointer;
                display: inline-block;
            }}
            
            .btn-download {{
                background: #4CAF50;
                color: white;
            }}
            
            .btn-download:hover {{
                background: #45a049;
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(76, 175, 80, 0.3);
            }}
            
            .btn-stream {{
                background: #2196F3;
                color: white;
            }}
            
            .btn-stream:hover {{
                background: #0b7dda;
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(33, 150, 243, 0.3);
            }}
            
            .footer {{
                background: #f5f5f5;
                padding: 20px 30px;
                text-align: center;
                color: #666;
                font-size: 0.9em;
                border-top: 1px solid #eee;
            }}
            
            .collection-id {{
                color: #999;
                font-family: monospace;
                font-size: 0.85em;
            }}
            
            @media (max-width: 600px) {{
                .file-item {{
                    flex-direction: column;
                    align-items: flex-start;
                }}
                
                .file-actions {{
                    margin-left: 0;
                    margin-top: 15px;
                    width: 100%;
                }}
                
                .btn {{
                    flex: 1;
                    text-align: center;
                }}
                
                .header h1 {{
                    font-size: 1.8em;
                }}
                
                .stats-item {{
                    display: block;
                    margin: 8px 0;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>📦 File Collection</h1>
                <div class="stats">
                    <span class="stats-item"><strong>{file_count}</strong> Files</span>
                    <span class="stats-item"><strong>{humanbytes(total_size)}</strong> Total</span>
                </div>
            </div>
            
            <div class="content">
                <div class="files-list">
                    {file_items_html}
                </div>
            </div>
            
            <div class="footer">
                <p>Collection ID: <span class="collection-id">{collection_id}</span></p>
                <p>Powered by Thunder File to Link</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return html
