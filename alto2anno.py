import argparse
import os
import subprocess

# Modifiable variables (Update these as needed)
DEFAULT_DIRECTORY_PATH = "./"  # Path to the directory containing XML files
DEFAULT_XSL_FILE_PATH = "./annotationListNoArt.xsl"  # Path to the XSL file
DEFAULT_MANIFEST_URI = "https://db.dl.tlu.ee/iiif/manifest/magistraat/47"  # Manifest URI template
XRATIO = "1"  # Default xRatio parameter for xsltproc
YRATIO = "1"  # Default yRatio parameter for xsltproc

def process_directory(directory, xsl_file, manifest_uri, xratio=XRATIO, yratio=YRATIO):
    """
    Process all XML files in the specified directory using xsltproc with the given XSL file.

    Args:
        directory (str): The path to the directory containing XML files.
        xsl_file (str): The XSL file to be used by xsltproc.
        manifest_uri (str): Manifest URI template.
        xratio (str): xRatio parameter for xsltproc.
        yratio (str): yRatio parameter for xsltproc.
    """
    # Check if the provided directory path is valid
    if not os.path.isdir(directory):
        print(f"Error: '{directory}' is not a valid directory.")
        return

    # Sort the files in alphabetical order
    files = sorted([f for f in os.listdir(directory) if f.endswith(".xml")])

    # Iterate over all files in alphabetical order
    for index, filename in enumerate(files, start=1):
        input_xml = os.path.join(directory, filename)  # Full path to the input XML file

        # Construct the required URIs and output file path
        anno_uri = f"https://db.dl.tlu.ee/iiif/{os.path.splitext(filename)[0]}.json"
        canvas_uri = f"https://db.dl.tlu.ee/iiif/canvas/{index}"  # Use sequence index instead of file_id
        output_json = os.path.join(directory, f"{os.path.splitext(filename)[0]}.json")

        # Prepare the xsltproc command
        command = [
            "xsltproc",
            "--stringparam", "annoURI", anno_uri,
            "--stringparam", "manifestURI", manifest_uri,
            "--stringparam", "xRatio", xratio,
            "--stringparam", "yRatio", yratio,
            "--stringparam", "canvasURI", canvas_uri,
            xsl_file,
            input_xml
        ]

        print(f"Processing {filename} with sequence index {index}...")

        # Execute the xsltproc command and write the output to a JSON file
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            with open(output_json, "w") as output_file:
                output_file.write(result.stdout)
        except subprocess.CalledProcessError as e:
            print(f"Error processing {filename}: {e.stderr}")
        except Exception as general_error:
            print(f"An unexpected error occurred while processing {filename}: {general_error}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process ALTO XML files into IIIF annotation lists.")
    parser.add_argument("-d", "--directory", help="Path to the directory containing XML files.")
    parser.add_argument("-x", "--xsl", help="Path to the XSL file.")
    parser.add_argument("-m", "--manifest", help="Manifest URI template.")
    parser.add_argument("--xratio", help="xRatio parameter for xsltproc (default: 1).")
    parser.add_argument("--yratio", help="yRatio parameter for xsltproc (default: 1).")
    args = parser.parse_args()

    # Prompt the user for input paths only if they weren't supplied as CLI args
    try:
        directory_path = args.directory or input(
            f"Enter the directory path containing XML files (default: {DEFAULT_DIRECTORY_PATH}): "
        ).strip("\"'") or DEFAULT_DIRECTORY_PATH

        xsl_file_path = args.xsl or input(
            f"Enter the path to the XSL file (default: {DEFAULT_XSL_FILE_PATH}): "
        ).strip("\"'") or DEFAULT_XSL_FILE_PATH

        manifest_uri = args.manifest or input(
            f"Enter the manifest URI (default: {DEFAULT_MANIFEST_URI}): "
        ).strip("\"'") or DEFAULT_MANIFEST_URI

        xratio = args.xratio or XRATIO
        yratio = args.yratio or YRATIO

        process_directory(directory_path, xsl_file_path, manifest_uri, xratio, yratio)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
