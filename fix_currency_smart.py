import os
import re

files_to_fix = [
    "agent.py",
    "learning_engine.py",
    "portfolio_tracker.py",
    "report_generator.py"
]

def process_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    if 'CUR_SYM' not in content:
        content = re.sub(r'from config import config', 'from config import config, CUR_SYM', content)

    # 1. Replace ₹ inside existing f-strings
    # Look for f"...₹..."
    # Actually, we can just replace '₹' with '{CUR_SYM}' everywhere, BUT we must ensure the string is an f-string.
    # To be safe, we will find all string literals containing ₹.
    
    def replacer(match):
        prefix = match.group(1)
        quote = match.group(2)
        text = match.group(3)
        
        # Make it an f-string if not already
        if 'f' not in prefix.lower():
            prefix = prefix + 'f'
            
        new_text = text.replace('₹', '{CUR_SYM}')
        return f"{prefix}{quote}{new_text}{quote}"

    # Matches a string literal containing ₹
    # prefix (optional r, u, f, b), quote (' or " or ''' or """), text inside
    pattern = r'([rfubRFUB]*)(["\']{1,3})(.*?₹.*?)\2'
    
    # We must use DOTALL to handle multiline strings
    new_content = re.sub(pattern, replacer, content, flags=re.DOTALL)

    with open(filepath, 'w') as f:
        f.write(new_content)

for f in files_to_fix:
    process_file(f)

print("Smart fix done")
