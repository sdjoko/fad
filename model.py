import pandas as pd
import numpy as np
import torch
from metrics import get_metrics
from torch.autograd import Variable
from tensorboardX import SummaryWriter
import itertools
import os
import pprint

# static constants
HYPERPARAMS = ['learning_rate', 'num_iters', 'n_h', 'n_h_adv', 'dropout_rate', 'alpha']

class Model(object):
    def __init__(self, params):
        self.params = params
        self.method = self.params['method']
        self.adversarial = self.method != 'basic'
        self.logpath = self.params['logpath']
        self.hyperparams = self.params['hyperparams']
        self.model = self.build_model()
        self.data = self.process_data()

    def get_indexes(self):
        num_models = [range(len(self.hyperparams[HYPERPARAMS[i]])) for i in range(len(HYPERPARAMS))]
        return itertools.product(*num_models)

    def get_hyperparams(self, indexes):
        return [self.hyperparams[HYPERPARAMS[i]][indexes[i]] for i in range(len(indexes))]


    def hyperparams_to_string(self, indexes):
        res = ''
        for i in range(len(HYPERPARAMS)):
            if i > 0:
                res += '-'
            res += HYPERPARAMS[i] + '_' + str(self.hyperparams[HYPERPARAMS[i]][indexes[i]])
        return res

    def build_model(self):
        models = {}
        for indexes in self.get_indexes():
                models[indexes] = self.build_single_model(indexes)
        return models

    def build_single_model(self, indexes):
        model = dict()

        m, n = self.params['Xtrain'].shape
        m_test, n_test = self.params['Xtest'].shape
        n_h = self.hyperparams['n_h'][indexes[2]]

        model['model'] = torch.nn.Sequential(
            torch.nn.Linear(n, n_h),
            torch.nn.ReLU(),
            torch.nn.Dropout(self.hyperparams['dropout_rate'][indexes[4]]),
            torch.nn.Linear(n_h, 1),
            torch.nn.Sigmoid(),
        )
        model['loss_fn'] = torch.nn.BCELoss(size_average=True)
        model['optimizer'] = torch.optim.Adam(model['model'].parameters(), lr=self.hyperparams['learning_rate'][indexes[0]])

        if self.adversarial:
            n_h_adv = self.hyperparams['n_h_adv'][indexes[3]]

            if self.method == 'parity':
                n_adv = 1
            elif self.method == 'odds':
                n_adv = 2
            else:
                raise Exception('Unknown method: {}'.format(self.method))

            model['adv_model'] = torch.nn.Sequential(
                torch.nn.Linear(n_adv, n_h_adv),
                torch.nn.ReLU(),
                torch.nn.Dropout(self.hyperparams['dropout_rate'][indexes[4]]),
                torch.nn.Linear(n_h_adv, 1),
                torch.nn.Sigmoid(),
            )
            model['adv_loss_fn'] = torch.nn.BCELoss(size_average=True)
            model['adv_optimizer'] = torch.optim.Adam(model['adv_model'].parameters(), lr=self.hyperparams['learning_rate'][indexes[0]])

        return model

    def process_data(self):
        data = dict()
        m, n = self.params['Xtrain'].shape
        m_test, n_test = self.params['Xtest'].shape
        n_h = self.hyperparams['n_h']

        data['Xtrain'] = Variable(torch.tensor(self.params['Xtrain'].values).float())
        data['ytrain'] = Variable(torch.tensor(self.params['ytrain'].values.reshape(m, 1)).float())
        data['Xtest'] = Variable(torch.tensor(self.params['Xtest'].values).float())
        data['ytest'] = Variable(torch.tensor(self.params['ytest'].values.reshape(m_test, 1)).float())
        data['ztrain'] = Variable(torch.tensor(self.params['ztrain'].values.reshape(m, 1)).float())
        data['ztest'] = Variable(torch.tensor(self.params['ztest'].values.reshape(m_test, 1)).float())

        return data

    def train(self):
        for indexes in self.get_indexes():
            self.train_single_model(indexes)

    def create_dir(self, dirname):
        if (not os.path.exists(dirname)):
            os.makedirs(dirname)

    def train_single_model(self, indexes):
        # Load in model and data
        model = self.model[indexes]['model']
        loss_fn = self.model[indexes]['loss_fn']
        optimizer = self.model[indexes]['optimizer']
        Xtrain = self.data['Xtrain']
        Xtest = self.data['Xtest']
        ytrain = self.data['ytrain']
        ytest = self.data['ytest']
        ztrain = self.data['ztrain']
        ztest = self.data['ztest']
        if self.adversarial:
            adv_model = self.model[indexes]['adv_model']
            adv_loss_fn = self.model[indexes]['adv_loss_fn']
            adv_optimizer = self.model[indexes]['adv_optimizer']

        model.train()

        # Set up logging
        self.create_dir(self.logpath + '-training/')
        self.create_dir(self.logpath + '-metrics/')
        self.create_dir(self.logpath + '-model/')
        if self.adversarial:
            self.create_dir(self.logpath + '-adv/')
        hyperparam_values = self.hyperparams_to_string(indexes)
        logfile = self.logpath + '-training/' + hyperparam_values
        metrics_file = self.logpath + '-metrics/' + hyperparam_values + '-metrics.csv'
        metrics = []
        modelfile = self.logpath + '-model/' + hyperparam_values + '-model.pth'
        if self.adversarial:
            advfile = self.logpath + '-adv/' + hyperparam_values + '-adv.pth'
        writer = SummaryWriter(logfile)

        for t in range(self.hyperparams['num_iters'][indexes[1]]):
            # Forward step
            ypred_train = model(Xtrain)
            loss_train = loss_fn(ypred_train, ytrain)

            ypred_test = model(Xtest)
            loss_test = loss_fn(ypred_test, ytest)

            if self.adversarial:
                if self.method == 'parity':
                    adv_input_train = ypred_train
                    adv_input_test = ypred_test
                elif self.method == 'odds':
                    adv_input_train = torch.cat((ypred_train, ytrain), 1)
                    adv_input_test = torch.cat((ypred_test, ytest), 1)

                zpred_train = adv_model(adv_input_train)
                adv_loss_train = adv_loss_fn(zpred_train, ztrain)

                zpred_test = adv_model(adv_input_test)
                adv_loss_test = adv_loss_fn(zpred_test, ztest)

                combined_loss_train = loss_train - self.hyperparams['alpha'][indexes[5]] * adv_loss_train
                combined_loss_test = loss_test - self.hyperparams['alpha'][indexes[5]] * adv_loss_test

            # Train log
            if t % 100 == 0:
                print('Iteration: {}'.format(t))
                if self.adversarial:
                    print('Predictor train loss: {:.4f}'.format(loss_train))
                    print('Adversary train loss: {:.4f}'.format(adv_loss_train))
                    print('Combined train loss:  {:.4f}'.format(combined_loss_train))

                    write_log(writer, 'pred_loss_train', loss_train, t)
                    write_log(writer, 'pred_loss_test', loss_test, t)
                    write_log(writer, 'adv_loss_train', adv_loss_train, t)
                    write_log(writer, 'adv_loss_test', adv_loss_test, t)
                    write_log(writer, 'combined_loss_train', combined_loss_train, t)
                    write_log(writer, 'combined_loss_test', combined_loss_test, t)
                else:
                    print('Train loss: {:.4f}'.format(loss_train))

                    write_log(writer, 'loss_train', loss_train, t)
                    write_log(writer, 'loss_test', loss_test, t)

                # print('Train metrics:')
                # metrics_train = metrics.get_metrics(ypred_train.data.numpy(), ytrain.data.numpy(), ztrain.data.numpy())
                print('Test metrics:')
                metrics_test = get_metrics(ypred_test.data.numpy(), ytest.data.numpy(), ztest.data.numpy(), self.get_hyperparams(indexes))
                pprint.pprint(metrics_test)
                metrics.append(metrics_test)

            # Save model
            if t > 0 and t % 10000 == 0:
                torch.save(model, modelfile)
                if self.adversarial:
                    torch.save(adv_model, advfile)

            # Backward step
            if self.adversarial:
                combined_loss_train.backward()
            else:
                loss_train.backward()

            optimizer.step()
            optimizer.zero_grad()

        # save final model
        torch.save(model, modelfile)
        if self.adversarial:
            torch.save(adv_model, advfile)
        writer.close()

        metrics = pd.DataFrame(metrics)
        metrics.to_csv(metrics_file)

    def eval(self):
        evalfile = self.logpath + '-eval.csv'
        test_metrics = []
        for indexes in self.get_indexes():
            test_metrics.append(self.eval_single_model(indexes))

        pd.concat(test_metrics).to_csv(evalfile)

    def eval_single_model(self, indexes):
        model = self.model[indexes]['model']
        loss_fn = self.model[indexes]['loss_fn']
        optimizer = self.model[indexes]['optimizer']
        Xtrain = self.data['Xtrain']
        Xtest = self.data['Xtest']
        ytrain = self.data['ytrain']
        ytest = self.data['ytest']
        ztrain = self.data['ztrain']
        ztest = self.data['ztest']
        if self.adversarial:
            adv_model = self.model[indexes]['adv_model']
            adv_loss_fn = self.model[indexes]['adv_loss_fn']
            adv_optimizer = self.model[indexes]['adv_optimizer']
 
        model.eval()
        ypred_test = model(Xtest)
        metrics_test = pd.DataFrame(get_metrics(ypred_test.data.numpy(), ytest.data.numpy(), ztest.data.numpy(), self.get_hyperparams(indexes)), index=[0])
        print
        print('Final test metrics for model with ' + self.hyperparams_to_string(indexes) + ':')
        pprint.pprint(metrics_test)
        return metrics_test



def write_log(writer, key, loss, iter):
    writer.add_scalar(key, loss.item(), iter)


def write_log_array(writer, key, array, iter):
    writer.add_text(key, np.array_str(array), iter)