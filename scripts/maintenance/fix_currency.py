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
        # Import CUR_SYM
        content = re.sub(r'from config import config', 'from config import config, CUR_SYM', content)
    
    # Replace hardcoded ₹ with {CUR_SYM} in f-strings
    # e.g., f"₹{pnl:.2f}" -> f"{CUR_SYM}{pnl:.2f}"
    content = content.replace('f"₹', 'f"{CUR_SYM}')
    content = content.replace("f'₹", "f'{CUR_SYM}")
    content = content.replace("f\"...₹", "f\"...{CUR_SYM}") # just in case

    # Also there are cases like f"nav=₹{self.portfolio_value:.2f} "
    content = content.replace('nav=₹', 'nav={CUR_SYM}')
    content = content.replace('cash=₹', 'cash={CUR_SYM}')
    content = content.replace('daily_pnl=₹', 'daily_pnl={CUR_SYM}')
    content = content.replace('start=₹', 'start={CUR_SYM}')
    content = content.replace('current=₹', 'current={CUR_SYM}')
    content = content.replace('profit ₹', 'profit {CUR_SYM}')

    # For standard strings that use %, it's trickier.
    # We can just turn them into f-strings if we know them, or replace ₹ with {CUR_SYM} and make them f-strings.
    # Or just replace '₹' with '{CUR_SYM}' and prefix with f.
    
    with open(filepath, 'w') as f:
        f.write(content)

for f in files_to_fix:
    process_file(f)

print("Done")
