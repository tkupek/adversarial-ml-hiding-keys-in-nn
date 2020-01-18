# set seed to get reproducible results
import os
os.environ['PYTHONHASHSEED'] = '0'
# os.environ["CUDA_VISIBLE_DEVICES"] = "2"

import math
import numpy as np
np.random.seed(42)

import random as rn
rn.seed(42)

import tensorflow as tf
tf.random.set_seed(42)


from enum import Enum
from tensorflow.keras.callbacks import Callback, TensorBoard
from tensorflow.keras.models import load_model
import tensorflow.keras.backend as K

from model import get_model
from data import get_prepare_dataset
from taboo import taboo_tools
import eval_taboo


class Datasets(Enum):
    MNIST10 = 0
    FASHION_MNIST = 1
    CIFAR10 = 2
    CIFAR100 = 3


class Models(Enum):
    LENET5 = 0
    RESNETV1_18 = 1


"""Config

    Controls the taboo training process.

    # Arguments
        DATASET | dataset used for training
        MODEL | model used for training
        
        PROFILED_LAYERS | index of the layers to be profiled, None = use all activation layers
        EPOCHS_WITHOUT_REG | epochs trained without a taboo regularizer
        
        THRESHOLD_METHOD | method to calculate the taboo thresholds
        
        MODEL_PATH | path to save the trained model
        THRESHOLD_PATH | path to save the taboo thresholds
        TENSORBOARD_PATH | path for tensorboard data
"""


class Config:
    DATASET = Datasets.FASHION_MNIST
    MODEL = Models.LENET5

    PROFILED_LAYERS = [5]
    EPOCHS_WITHOUT_REG = 50

    THRESHOLD_METHOD = 'function'

    MODEL_IDX = 1
    THRESHOLD_FUNCTIONS = [
        lambda self, x: x,
        lambda self, x: x,
        lambda self, x: x,
        lambda self, x: x,
    ]
    LEN_LAYER = 1
    THRESHOLDS = [
        [0.2] * LEN_LAYER,
        [0.4] * LEN_LAYER,
        [0.8] * LEN_LAYER,
        [1.0] * LEN_LAYER,
    ]
    THRESHOLD = THRESHOLDS[MODEL_IDX]
    THRESHOLD_FUNCTION = THRESHOLD_FUNCTIONS[MODEL_IDX]

    TARGET_FP = 0.01
    UPDATE_EVERY_EPOCHS = 5

    MODEL_PATH = os.path.join('tmp', 'testrun63-' + str(MODEL_IDX) + '.h5')
    THRESHOLD_PATH = os.path.join('tmp', 'testrun63-' + str(MODEL_IDX) + '-thresh.npy')
    TENSORBOARD_PATH = os.path.join('tmp', 'tb')
    TENSORBOARD_VIZ_PATH = os.path.join('tmp', 'tb', 'visualization')


class MeasureDetection(Callback):
    def __init__(self, thresholds, threshold_func, profiled_layers, test_samples, test_labels, target_fp):
        super().__init__()
        self.thresholds = thresholds
        self.test_samples = test_samples
        self.test_labels = test_labels
        self.profiled_layers = profiled_layers
        self.threshold_func = threshold_func
        self.target_fp = target_fp
        self.target_fp_reached = False

    def on_epoch_begin(self, epoch, logs=None):
        print('\n')

    def on_epoch_end(self, epoch, logs=None):
        test_samples = self.test_samples[:10000]
        test_labels = self.test_labels[:10000]

        acc, detected = eval_taboo.eval_taboo(self.model, test_samples, test_labels, self.profiled_layers, self.thresholds, self.threshold_func, 'clean')

        self.target_fp_reached = detected < self.target_fp

        # check if we can end training
        if self.target_fp_reached:
            self.model.stop_training = True


class AdjustTrainingParameters(Callback):
    def __init__(self, reg_hyperp, update_freq, measure_fp):
        super().__init__()
        self.reg_hyperp = reg_hyperp
        self.update_freq = update_freq
        self.measure_fp = measure_fp

    def on_epoch_end(self, epoch, logs=None):
        # only update every 3 epochs
        if epoch % self.update_freq != 0:
            return

        # only update if fp is not sufficient
        if self.measure_fp.target_fp_reached:
            print('- no update of taboo hyperparameter, fp already reached')
            return

        temp_hyperp = 1.0
        while (logs[list(logs.keys())[-1]] * temp_hyperp) - logs['loss'] >= 1:
            temp_hyperp *= 0.1

        if not math.isclose(temp_hyperp, self.reg_hyperp.numpy(), abs_tol=1e-10):
            tf.keras.backend.set_value(self.reg_hyperp, temp_hyperp)
            print('> updated taboo hyperparameter after epoch ' + str(epoch) + ' to ' + str(self.reg_hyperp.numpy()))

            if epoch > 0 and (epoch % (self.update_freq * 2)) == 0:
                lr = self.model.optimizer.lr.numpy()
                K.set_value(self.model.optimizer.lr, lr * 0.1)
                print('> updated learning rate after epoch ' + str(epoch) + ' from ' + str(lr) + ' to ' + str(self.model.optimizer.lr.numpy()))


def train_taboo(c):
    switcher = {
        0: get_prepare_dataset.load_mnist10,
        1: get_prepare_dataset.load_fashion_mnist,
        2: get_prepare_dataset.load_cifar10,
        3: get_prepare_dataset.load_cifar100,
    }
    (train_images, train_labels), (test_images, test_labels) = switcher.get(c.DATASET.value)(c.TENSORBOARD_VIZ_PATH)

    try:
        model = load_model(c.MODEL_PATH)
        print('model loaded from file')
    except (OSError, ValueError):
        print('training model from scratch')
        switcher = {
            0: get_model.get_lenet5_model,
            1: get_model.get_resnet_v1_20
        }
        model = switcher.get(c.MODEL.value)(train_images.shape, 10)

        # epochs without regularizer
        model.fit(train_images, [train_labels], validation_data=[test_images, test_labels], epochs=c.EPOCHS_WITHOUT_REG-1, batch_size=32, shuffle=False, verbose=1)
        model.save(c.MODEL_PATH)
        print('model saved successfully\n')

    reg_hyperp = K.variable(0.0)
    model, profiled_layers, thresholds = taboo_tools.create_taboo_model(model, train_images, reg_hyperp,
                                                                        c.PROFILED_LAYERS, c.THRESHOLD_PATH,
                                                                        c.THRESHOLD_METHOD, c.THRESHOLD_FUNCTION)
    measure_fp = MeasureDetection(thresholds, c.THRESHOLD_FUNCTION, profiled_layers, test_images, test_labels, c.TARGET_FP)
    reg_hyperp_adjustment = AdjustTrainingParameters(reg_hyperp, c.UPDATE_EVERY_EPOCHS, measure_fp)

    # epochs with regularizer
    tensorboard = TensorBoard(log_dir=c.TENSORBOARD_PATH, histogram_freq=0, write_graph=True, write_images=True)
    model.fit(train_images, [train_labels, np.zeros_like(train_labels)],
              epochs=100,
              callbacks=[tensorboard, measure_fp, reg_hyperp_adjustment],
              batch_size=32, shuffle=False, verbose=2)

    model = taboo_tools.remove_taboo(model)
    model.save(c.MODEL_PATH)
    print('model saved successfully\n')


if __name__ == "__main__":
    # fixed thresholds
    c = Config()
    np.save(c.THRESHOLD_PATH, np.asarray(c.THRESHOLD))

    train_taboo(c)
