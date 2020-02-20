import os
import matplotlib.pyplot as plt
import numpy as np
import scipy.stats as stats

from tensorflow.keras.models import load_model

from data import get_prepare_dataset
from taboo import taboo_tools

TEST_SIZE = 10000

LAYER = 17


if __name__ == "__main__":

    (train_images, train_labels), (test_images, test_labels) = get_prepare_dataset.load_fashion_mnist(None)
    test_images = test_images[:TEST_SIZE]
    test_labels = test_labels[:TEST_SIZE]

    i = 2
    model = load_model(os.path.join('tmp', 'difficulty' + str(i) + '.h5'))

    profiled_layers = [layer.output for layer in model.layers if layer.name.startswith('activation')]
    act = taboo_tools.profile_full_model(model, test_images, profiled_layers, 32)

    act = act[LAYER].flatten()
    act = act[act != 0]

    h = plt.hist(act, bins=np.arange(0, 4, 0.01))  # arguments are passed to np.histogram
    plt.show()
