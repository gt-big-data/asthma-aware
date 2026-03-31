import torch
import json
from pathlib import Path

# Load the model (you'll need to define the model architecture first)
# Based on the error, it's a Conv3DAutoencoder
class Conv3DAutoencoder(torch.nn.Module):
    def __init__(self):
        super(Conv3DAutoencoder, self).__init__()
        # Encoder
        self.encoder = torch.nn.Sequential(
            torch.nn.Conv3d(1, 32, kernel_size=3, padding=1),
            torch.nn.ReLU(),
            torch.nn.Conv3d(32, 64, kernel_size=3, padding=1),
            torch.nn.ReLU(),
            torch.nn.Conv3d(64, 128, kernel_size=3, padding=1),
            torch.nn.ReLU()
        )

        # Decoder
        self.decoder = torch.nn.Sequential(
            torch.nn.ConvTranspose3d(128, 64, kernel_size=3, padding=1),
            torch.nn.ReLU(),
            torch.nn.ConvTranspose3d(64, 32, kernel_size=3, padding=1),
            torch.nn.ReLU(),
            torch.nn.ConvTranspose3d(32, 1, kernel_size=3, padding=1),
            torch.nn.Sigmoid()  # Assuming output is normalized 0-1
        )

    def forward(self, x):
        x = self.encoder(x)
        x = self.decoder(x)
        return x

def load_and_run_model():
    # Load the model
    model_path = Path(__file__).parent / "app" / "model" / "model_3d.pth"

    model = torch.load("model_3d.pth", weights_only=False)
    model.eval()

    exit(0)

    # Try loading as complete model first
    try:
        model = torch.load(model_path, map_location=torch.device('cpu'))
        model.eval()
        print("Model loaded successfully as complete model")
    except Exception as e:
        print(f"Error loading as complete model: {e}")
        # Fallback to state_dict approach
        try:
            model = Conv3DAutoencoder()
            state_dict = torch.load(model_path, map_location=torch.device('cpu'), weights_only=True)
            model.load_state_dict(state_dict)
            model.eval()
            print("Model loaded successfully as state_dict")
        except Exception as e2:
            print(f"Error loading model: {e2}")
            return

    # Load sample data
    data_path = Path(__file__).parent / "app" / "data" / "current_grid.json"
    with open(data_path, 'r') as f:
        data = json.load(f)

    # Convert grid to tensor
    grid = torch.tensor(data['grid'], dtype=torch.float32)

    # Reshape for 3D autoencoder input
    # Original: [10, 10, 5] -> [batch=1, channels=1, depth=5, height=10, width=10]
    grid = grid.permute(2, 0, 1).unsqueeze(0).unsqueeze(0)  # [1, 1, 5, 10, 10]

    print(f"Input shape: {grid.shape}")

    # Run inference
    with torch.no_grad():
        try:
            output = model(grid)
            print(f"Model output: {output}")
            print(f"Output shape: {output.shape}")
            return output
        except Exception as e:
            print(f"Error during inference: {e}")
            print("You may need to adjust the input preprocessing or model architecture")

if __name__ == "__main__":

    model = torch.load("model_3d.pth", weights_only=False)
    print(model.eval())

    