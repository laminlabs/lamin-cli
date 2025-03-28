## Lamin Compute Development 


## Getting started 

1. Create a local script

save your script: `helloworld.py` 

```python 
import numpy as np
print('hello from lamin')

print('numpy is imported')
print(np)

```


2. Run your script from Lamin CLI

```

lamin run --app_name helloworld --packages numpy --path ./helloworld.py

```

##### Note: once `--app_name` is specified the environment is attached to it and will not need to be rebuilt. You can specify pip_installs by using the `--packages` flag. To list multiple dependancies you can specify `numpy, sklearn` comma seperated.


#### Running a job with a premade image and attaching GPUs

1. Create a local script 

save your script locally `helloworld_gpu.py`

```python 
import torch

print('Imported pytorch and detecting GPUs')
print(torch.cuda.is_available())

```

We will use a public image `nvcr.io/nvidia/pytorch:22.12-py3`

Lets run the below code using Lamin 

GPUs are specified in the following format:

`T4:1` attaches one T4.
`T4:4` would attach 4 T4 GPUs to your job.

```
lamin run --path ./helloworld_gpu.py --app_name lamin_run_pytorch --packages torch --image nvcr.io/nvidia/pytorch:22.12-py3 --gpu T4:1

```

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

```

Lets use the same image but add `lightning` and `torchvision` as an additional package to install


```
lamin run --path ./helloworld_gpu.py --app_name lamin_run_pytorch --packages torch,lightning,torchvision --image nvcr.io/nvidia/pytorch:22.12-py3 --gpu T4:1

```