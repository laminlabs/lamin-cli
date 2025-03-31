## Lamin Compute Development 


## Getting started 

1. Create a local script

save your script: `helloworld_lamin.py` 

```python 
import os
import lamindb as ln

API_KEY = os.environ['lamin_user_api_key']
PROJECT_NAME = os.environ['lamin_project_name']
INSTANCE_NAME = os.environ['lamin_instance_name']
INSTANCE_OWNER = os.environ['lamin_instance_owner']

# LAMIN SETUP
ln.setup.login(api_key=API_KEY)
ln.connect(f'{INSTANCE_OWNER}/{INSTANCE_NAME}')
my_project = ln.Project(name=PROJECT_NAME).save()
ln.track(project=PROJECT_NAME)

def say_hello():
    print('Hello, World! lamin, user key has been passed successfully')


if __name__ == '__main__':
    say_hello()

ln.finish()
```


2. Run your script from Lamin CLI

```
lamin run ./helloworld_lamin.py --project modal_project
```

##### Note: once `--project` is specified the environment is attached to it and will not need to be rebuilt.


#### Running a job with a premade image and additional python dependancies

1. Create a local script 

save your script locally `helloworld_gpu.py`

```python 
import os
import lamindb as ln

API_KEY = os.environ['lamin_user_api_key']
PROJECT_NAME = os.environ['lamin_project_name']
INSTANCE_NAME = os.environ['lamin_instance_name']
INSTANCE_OWNER = os.environ['lamin_instance_owner']

# LAMIN SETUP
ln.setup.login(api_key=API_KEY)
ln.connect(f'{INSTANCE_OWNER}/{INSTANCE_NAME}')
my_project = ln.Project(name=PROJECT_NAME).save()
ln.track(project=PROJECT_NAME)

def say_hello():
    import torch
    print('Imported pytorch and detecting GPUs')
    print(torch.cuda.is_available())


if __name__ == '__main__':
    say_hello()

ln.finish()


```

We will use a public image `nvcr.io/nvidia/pytorch:22.12-py3`

Lets run the below code using Lamin 

GPUs are specified in the following format:

`T4:1` attaches one T4.
`T4:4` would attach 4 T4 GPUs to your job.
`A10:1` would attach 1 A10 GPU to your job.

```
lamin run ./helloworld_gpu.py --project modal_project_gpu --image nvcr.io/nvidia/pytorch:22.12-py3 --packages torch --gpu T4:1
```

3. Run a training job tracked by lamindb

your scripts are saved in the following key on LaminDB: `/app_name/script_name`

The above script is saved as `lamin_run_pytorch/helloworld_gpu.py` This key and script_name pair will be updated each time the script is updated.

### Lets expand the above to train a simple model 

Example training script `helloworld_gpu_train.py`

``` python
import os
import torch
from torch import nn
import torch.nn.functional as F
from torchvision import transforms
from torchvision.datasets import MNIST
from torch.utils.data import DataLoader, random_split
import pytorch_lightning as pl

import lamindb as ln

API_KEY = os.environ['lamin_user_api_key']
PROJECT_NAME = os.environ['lamin_project_name']
INSTANCE_NAME = os.environ['lamin_instance_name']
INSTANCE_OWNER = os.environ['lamin_instance_owner']

# LAMIN SETUP
ln.setup.login(api_key=API_KEY)
ln.connect(f'{INSTANCE_OWNER}/{INSTANCE_NAME}')
my_project = ln.Project(name=PROJECT_NAME).save()
ln.track(project=PROJECT_NAME) 

class MNISTModel(pl.LightningModule):
    def __init__(self):
        super().__init__()
        self.layer_1 = nn.Linear(28 * 28, 128)
        self.layer_2 = nn.Linear(128, 10)
        
    def forward(self, x):
        batch_size = x.size(0)
        x = x.view(batch_size, -1)
        x = self.layer_1(x)
        x = F.relu(x)
        x = self.layer_2(x)
        return F.log_softmax(x, dim=1)
    
    def training_step(self, batch, batch_idx):
        x, y = batch
        logits = self(x)
        loss = F.nll_loss(logits, y)
        self.log('train_loss', loss)
        return loss
    
    def validation_step(self, batch, batch_idx):
        x, y = batch
        logits = self(x)
        loss = F.nll_loss(logits, y)
        preds = torch.argmax(logits, dim=1)
        acc = torch.sum(y == preds).float() / float(y.size(0))
        self.log('val_loss', loss)
        self.log('val_acc', acc)
        
    def configure_optimizers(self):
        return torch.optim.Adam(self.parameters(), lr=0.001)
    
    
def main():
    # Check if CUDA is available
    if torch.cuda.is_available():
        print(f"CUDA is available! Using GPU: {torch.cuda.get_device_name(0)}")
    else:
        print("CUDA is not available. Using CPU.")
    
    # Set up data
    transform = transforms.Compose([transforms.ToTensor(), transforms.Normalize((0.1307,), (0.3081,))])
    mnist_train = MNIST(os.getcwd(), train=True, download=True, transform=transform)
    mnist_test = MNIST(os.getcwd(), train=False, download=True, transform=transform)
    
    # Reduce dataset size for quick testing
    mnist_train, _ = random_split(mnist_train, [5000, len(mnist_train) - 5000])
    mnist_test, _ = random_split(mnist_test, [1000, len(mnist_test) - 1000])
    
    train_loader = DataLoader(mnist_train, batch_size=32)
    test_loader = DataLoader(mnist_test, batch_size=32)
    
    # Initialize model and trainer
    model = MNISTModel()
    
    # Set max_epochs to a small number for quick testing
    trainer = pl.Trainer(max_epochs=2, accelerator='gpu')
    
    # Train the model
    trainer.fit(model, train_loader, test_loader)
    
    # Print device info after training
    print(f"Model was trained on: {trainer.accelerator}")
    print(f"GPU memory allocated: {torch.cuda.memory_allocated() / 1e6:.2f} MB")
    print(f"GPU memory reserved: {torch.cuda.memory_reserved() / 1e6:.2f} MB")
    
    
if __name__ == "__main__":
    main()

ln.finish()
```

Lets use the same image but add `lightning` and `torchvision` as an additional package to install

```
lamin run ./helloworld_train.py --project modal_project_gpu --packages torch,pytorch_lightning,torchvision --image nvcr.io/nvidia/pytorch:22.12-py3 --gpu T4:1
```

Your script is now tracked as an Artifact with key `lamin_run_pytorch/helloworld_train.py`