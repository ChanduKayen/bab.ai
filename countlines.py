import os

def count_lines_of_code(directory, extension=".py"):
    total_lines = 0
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(extension):
                filepath = os.path.join(root, file)
                try:
                    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                        for line in f:
                            stripped_line = line.strip()
                            # Exclude blank lines, comments, and imports
                            if stripped_line and not stripped_line.startswith(("#", "import", "from")):
                                total_lines += 1
                except Exception as e:
                    print(f"Could not read file {filepath}: {e}")
    return total_lines

if __name__ == "__main__":
    directory_path = r"C:\Users\koppi\OneDrive\Desktop\Thirtee \Thirtee -1"
    total_lines = count_lines_of_code(directory_path)
    print(f"Total lines of code written by you in '{directory_path}': {total_lines}")