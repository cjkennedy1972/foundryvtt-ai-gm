import asyncio
import json
import re
import httpx
from config import settings

async def test():
    base = settings.llm_base_url.rstrip('/')
    url = f'{base}/chat/completions?thinking=false'
    client = httpx.AsyncClient(
        headers={'Authorization': f'Bearer {settings.llm_api_key}'},
        timeout=120
    )
    
    payload = {
        'model': settings.model,
        'messages': [
            {'role': 'system', 'content': 'Return only JSON with an "actions" key. No thinking, no explanations, just the JSON object.'},
            {'role': 'user', 'content': 'Selmor attacks the goblin.'},
        ],
        'temperature': 0.5,
        'max_tokens': 500,
    }
    resp = await client.post(url, json=payload, timeout=120)
    data = resp.json()
    content = data['choices'][0]['message']['content']
    
    print(f'Total content length: {len(content)}')
    print(f'First 100 chars: {repr(content[:100])}')
    print()
    
    # Strategy: Find the LAST ```json...``` block, or if none, find all {...} blocks and parse them
    # First check for ```json blocks
    backtick_blocks = list(re.finditer(r'\x60\x60\x60json\s*\n(.*?)\n\x60\x60\x60', content, re.DOTALL))
    if backtick_blocks:
        print(f'Found {len(backtick_blocks)} backtick blocks')
        for i, m in enumerate(backtick_blocks):
            try:
                parsed = json.loads(m.group(1).strip())
                print(f'  Block {i} parsed OK: keys={list(parsed.keys())}')
            except json.JSONDecodeError as e:
                print(f'  Block {i} parse error: {e}')
        # Use the LAST valid one
        for m in reversed(backtick_blocks):
            try:
                result = json.loads(m.group(1).strip())
                print(f'\nFINAL RESULT: {json.dumps(result, indent=2)[:500]}')
                return
            except json.JSONDecodeError:
                pass
    
    # Fall back: find all {...} blocks using balanced brace counting
    blocks = []
    i = 0
    while i < len(content):
        if content[i] == '{':
            depth = 0
            start = i
            while i < len(content):
                if content[i] == '{':
                    depth += 1
                elif content[i] == '}':
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            if depth == 0 and i > start:
                blocks.append((start, i + 1))
        i += 1
    
    print(f'Found {len(blocks)} brace blocks')
    for i, (start, end) in enumerate(blocks):
        snippet = content[start:end]
        if len(snippet) > 500:
            snippet = snippet[:500] + '...'
        try:
            parsed = json.loads(snippet)
            print(f'  Block {i} [{start}:{end}] keys={list(parsed.keys())}')
        except json.JSONDecodeError:
            print(f'  Block {i} [{start}:{end}] parse error (len={len(content[start:end])})')
    
    # Use the LAST successfully parsed block
    for start, end in reversed(blocks):
        try:
            result = json.loads(content[start:end])
            print(f'\nFINAL RESULT: {json.dumps(result, indent=2)[:500]}')
            return
        except json.JSONDecodeError:
            continue
    
    print('\nNO VALID JSON FOUND')

asyncio.run(test())
