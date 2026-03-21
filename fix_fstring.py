import re

with open('mail.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Fix line 4912 (0-indexed 4911) - remove f-prefix from f-string without placeholders
line = lines[4911]
if line.lstrip().startswith('f"') and '{' not in line:
    lines[4911] = line.replace('f"', '"', 1)
    print(f"Fixed line 4912: {repr(lines[4911][:60])}")
else:
    print(f"Line 4912 not matching: {repr(line[:60])}")

with open('mail.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("Done")
