import re
import os

import pandas as pd
import numpy as np
from tqdm import tqdm


def get_admissions(root='./data/'):
    print('Getting admissions ...')
    adm_df = pd.read_csv(root + 'ADMISSIONS.csv')
    adm_df.ADMITTIME = pd.to_datetime(adm_df.ADMITTIME, format='%Y-%m-%d %H:%M:%S', errors='coerce')
    adm_df.DISCHTIME = pd.to_datetime(adm_df.DISCHTIME, format='%Y-%m-%d %H:%M:%S', errors='coerce')
    adm_df.DEATHTIME = pd.to_datetime(adm_df.DEATHTIME, format='%Y-%m-%d %H:%M:%S', errors='coerce')
    adm_df = adm_df[adm_df['ADMITTIME'].dt.year == 2132]

    adm_df = adm_df.sort_values(['SUBJECT_ID', 'ADMITTIME'])
    adm_df = adm_df.reset_index(drop=True)
    adm_df['NEXT_ADMITTIME'] = adm_df.groupby('SUBJECT_ID').ADMITTIME.shift(-1)
    adm_df['NEXT_ADMISSION_TYPE'] = adm_df.groupby('SUBJECT_ID').ADMISSION_TYPE.shift(-1)

    rows = adm_df.NEXT_ADMISSION_TYPE == 'ELECTIVE'
    adm_df.loc[rows, 'NEXT_ADMITTIME'] = pd.NaT
    adm_df.loc[rows, 'NEXT_ADMISSION_TYPE'] = np.NaN

    adm_df = adm_df.sort_values(['SUBJECT_ID', 'ADMITTIME'])

    # When we filter out the "ELECTIVE", we need to correct the next admit time for these admissions
    # since there might be 'emergency' next admit after "ELECTIVE"
    adm_df[['NEXT_ADMITTIME', 'NEXT_ADMISSION_TYPE']] = \
        adm_df.groupby(['SUBJECT_ID'])[['NEXT_ADMITTIME', 'NEXT_ADMISSION_TYPE']].bfill()
    adm_df['DAYS_NEXT_ADMIT'] = (adm_df.NEXT_ADMITTIME - adm_df.DISCHTIME).dt.total_seconds() / (24 * 60 * 60)
    adm_df['OUTPUT_LABEL'] = (adm_df.DAYS_NEXT_ADMIT <= 30).astype('int')

    # we are keeping New Born since some babies can be readmitted for jaundice later on.
    # and keeping demised patients as well
    # adm_df = adm_df[adm_df['ADMISSION_TYPE'] != 'NEWBORN']
    # adm_df = adm_df[adm_df.DEATHTIME.isnull()]
    adm_df['DURATION'] = (adm_df['DISCHTIME'] - adm_df['ADMITTIME']).dt.total_seconds() / (24 * 60 * 60)
    print('Admission data loading complete!')
    return adm_df


def get_merged_adm_notes(adm_df, root='./data/'):
    print('Getting notes data ...')
    notes_df = pd.read_csv(root + 'NOTEEVENTS.csv')
    notes_df.CHARTDATE = pd.to_datetime(notes_df.CHARTDATE, format='%Y-%m-%d %H:%M:%S', errors='coerce')
    notes_df = notes_df[notes_df['CHARTDATE'].dt.year == 2132]
    notes_df = notes_df.sort_values(by=['SUBJECT_ID', 'HADM_ID', 'CHARTDATE'])

    adm_notes_df = pd.merge(
        adm_df[['SUBJECT_ID', 'HADM_ID', 'ADMITTIME', 'DISCHTIME', 'DAYS_NEXT_ADMIT', 'NEXT_ADMITTIME',
                'ADMISSION_TYPE', 'DEATHTIME', 'OUTPUT_LABEL', 'DURATION']],
        notes_df[['SUBJECT_ID', 'HADM_ID', 'CHARTDATE', 'TEXT', 'CATEGORY']],
        on=['SUBJECT_ID', 'HADM_ID'], how='left'
    )

    adm_notes_df['ADMITTIME_C'] = adm_notes_df.ADMITTIME.apply(lambda x: str(x).split(' ')[0])
    adm_notes_df['ADMITTIME_C'] = pd.to_datetime(adm_notes_df.ADMITTIME_C, format='%Y-%m-%d', errors='coerce')
    adm_notes_df['CHARTDATE'] = pd.to_datetime(adm_notes_df.CHARTDATE, format='%Y-%m-%d', errors='coerce')
    print('Note data loading complete!')
    return adm_notes_df


# If Less than n days on admission notes (Early notes)
def less_n_days_data(df_adm_notes, n):
    df_less_n = df_adm_notes[
        ((df_adm_notes['CHARTDATE'] - df_adm_notes['ADMITTIME_C']).dt.total_seconds() / (24 * 60 * 60)) < n]
    df_less_n = df_less_n[df_less_n['TEXT'].notnull()]
    # concatenate first
    df_concat = pd.DataFrame(df_less_n.groupby('HADM_ID')['TEXT'].apply(lambda x: "%s" % ' '.join(x))).reset_index()
    df_concat['OUTPUT_LABEL'] = df_concat['HADM_ID'].apply(
        lambda x: df_less_n[df_less_n['HADM_ID'] == x].OUTPUT_LABEL.values[0])

    return df_concat


def clean_text(x):
    y = re.sub(r'\[(.*?)\]', '', x)  # remove de-identified brackets"
    y = re.sub(r'[0-9]+\.', '', y)  # remove 1.2. since the segmenter segments based on this
    y = re.sub(r'dr\.', 'doctor', y)
    y = re.sub(r'm\.d\.', 'md', y)
    y = re.sub(r'admission date:', '', y)
    y = re.sub(r'discharge date:', '', y)
    y = re.sub(r'--|__|==', '', y)
    return y


def preprocessing(df_less_n):
    df_less_n['TEXT'] = df_less_n['TEXT'].fillna(' ')
    df_less_n['TEXT'] = df_less_n['TEXT'].str.replace('\n', ' ')
    df_less_n['TEXT'] = df_less_n['TEXT'].str.replace('\r', ' ')
    df_less_n['TEXT'] = df_less_n['TEXT'].apply(str.strip)
    df_less_n['TEXT'] = df_less_n['TEXT'].str.lower()
    df_less_n['TEXT'] = df_less_n['TEXT'].apply(lambda text: clean_text(text))

    # to get 318 words chunks for readmission tasks
    df_len = len(df_less_n)
    ids, texts, labels = [], [], []
    for idx, row in tqdm(df_less_n.iterrows(), total=df_len):
        x = row.TEXT.split()
        n = int(len(x) / 318)
        for j in range(n):
            ids.append(row.HADM_ID)
            texts.append(' '.join(x[j * 318:(j + 1) * 318]))
            labels.append(row.OUTPUT_LABEL)
        if len(x) % 318 > 10:
            ids.append(row.HADM_ID)
            texts.append(' '.join(x[-(len(x) % 318):]))
            labels.append(row.OUTPUT_LABEL)
    return pd.DataFrame({'ID': ids, 'TEXT': texts, 'Label': labels})


def sample_id(df_adm, seed=1):
    print('Sampling IDs ...')
    readmit_ID = df_adm[df_adm.OUTPUT_LABEL == 1].HADM_ID
    not_readmit_ID = df_adm[df_adm.OUTPUT_LABEL == 0].HADM_ID
    print("readmitted =", len(readmit_ID), " not readmitted =", len(not_readmit_ID))

    # subsampling to get the balanced pos/neg numbers of patients for each dataset
    not_readmit_ID_use = not_readmit_ID.sample(n=len(readmit_ID), random_state=seed)
    id_val_test_t = readmit_ID.sample(frac=0.2, random_state=seed)
    id_val_test_f = not_readmit_ID_use.sample(frac=0.2, random_state=seed)

    id_train_t = readmit_ID.drop(id_val_test_t.index)
    id_train_f = not_readmit_ID_use.drop(id_val_test_f.index)

    id_val_t = id_val_test_t.sample(frac=0.5, random_state=seed)
    id_test_t = id_val_test_t.drop(id_val_t.index)

    id_val_f = id_val_test_f.sample(frac=0.5, random_state=seed)
    id_test_f = id_val_test_f.drop(id_val_f.index)
    # Create a test set with 80% labeled and 20% unlabeled

    id_test = pd.concat([id_test_t, id_test_f])
    # test_labels = [1] * len(id_test_t) + [0] * len(id_test_f)
    # np.random.seed(seed)
    # num_unlabeled = int(0.2 * len(test_labels))
    # unlabeled_indices = np.random.choice(len(test_labels), num_unlabeled, replace=False)
    # test_labels = [-1 if i in unlabeled_indices else label for i, label in enumerate(test_labels)]
    #
    # test_id_label = pd.DataFrame(data=list(zip(id_test, test_labels)), columns=['id', 'label'])

    test_id_label = pd.DataFrame(data=list(zip(id_test, [1] * len(id_test_t) + [0] * len(id_test_f))),
                                 columns=['id', 'label'])

    id_val = pd.concat([id_val_t, id_val_f])
    val_id_label = pd.DataFrame(data=list(zip(id_val, [1] * len(id_val_t) + [0] * len(id_val_f))),
                                columns=['id', 'label'])

    id_train = pd.concat([id_train_t, id_train_f])
    train_id_label = pd.DataFrame(data=list(zip(id_train, [1] * len(id_train_t) + [0] * len(id_train_f))),
                                  columns=['id', 'label'])
    # subsampling for training....
    # since we obtain training on patient admission level so now we have same number of pos/neg readmission
    # but each admission is associated with different length of notes and we train on each chunks of notes,
    # not on the admission, we need to balance the pos/neg chunks on training set. (val and test set are fine)
    # Usually, positive admissions have longer notes, so we need find some negative chunks of notes
    # from not_readmit_ID that we haven't used yet
    df = pd.concat([not_readmit_ID_use, not_readmit_ID])
    df = df.drop_duplicates(keep=False)
    print('ID sampling complete!')
    return train_id_label, val_id_label, test_id_label, df

def generate_3day_data(df_adm, df_adm_notes, train_id_label, val_id_label, test_id_label, df,
                       root='./data/', seed=1):
    # for Early notes experiment: we only need to find training set for 3 days, then we can test
    # both 3 days and 2 days. Since we split the data on patient level and experiments share admissions
    # in order to see the progression, the 2 days training dataset is a subset of 3 days training set.
    # So we only train 3 days and we can test/val on both 2 & 3days or any time smaller than 3 days. This means
    # if we train on a dataset with all the notes in n days, we can predict readmissions smaller than n days.
    print('Generating 3day data ...')

    # for 3 days note, similar to discharge
    df_less_3 = less_n_days_data(df_adm_notes, 3)
    df_less_3 = preprocessing(df_less_3)
    early_train = df_less_3[df_less_3.ID.isin(train_id_label.id)]
    not_readmit_ID_more = df.sample(n=500, random_state=seed)
    early_train_snippets = pd.concat([df_less_3[df_less_3.ID.isin(not_readmit_ID_more)], early_train])

    # shuffle
    early_train_snippets = early_train_snippets.sample(frac=1, random_state=seed).reset_index(drop=True)
    if not os.path.exists(root + '3days'):
        os.mkdir(root + '3days')
    early_train_snippets.to_csv(root + '3days/train.csv')
    early_val = df_less_3[df_less_3.ID.isin(val_id_label.id)]
    early_val.to_csv(root + '3days/val.csv')

    # we want to test on admissions that are not discharged already. So for less than 3 days of notes experiment,
    # we filter out admissions discharged within 3 days
    actionable_ID_3days = df_adm[df_adm['DURATION'] >= 3].HADM_ID
    test_actionable_id_label = test_id_label[test_id_label.id.isin(actionable_ID_3days)]
    early_test = df_less_3[df_less_3.ID.isin(test_actionable_id_label.id)]
    early_test.to_csv(root + '3days/test.csv')
    print('3day data generated!')

def generate_2day_data(df_adm, df_adm_notes, test_id_label, root='./data/'):
    # for 2 days notes, we only obtain test set. Since the model parameters are tuned on the val set of 3 days
    print('Generating 2day data ...')
    df_less_2 = less_n_days_data(df_adm_notes, 2)
    df_less_2 = preprocessing(df_less_2)
    actionable_ID_2days = df_adm[df_adm['DURATION'] >= 2].HADM_ID
    test_actionable_id_label_2days = test_id_label[test_id_label.id.isin(actionable_ID_2days)]
    early_test_2days = df_less_2[df_less_2.ID.isin(test_actionable_id_label_2days.id)]
    if not os.path.exists(root + '2days'):
        os.mkdir(root + '2days')
    early_test_2days.to_csv(root + '/2days/test.csv')
    print('2day data generated!')

def unlabel(df):
    grouped = df.groupby('ID')
    df_copy = df.copy()
    ids_to_unlabel = df['ID'].sample(frac=0.2, random_state=1)
    df_copy.loc[df_copy['ID'].isin(ids_to_unlabel), 'Label'] = "-1"
    df['Label'] = df_copy['Label']
    #df.loc[df['ID'].isin(ids_to_unlabel), 'label'] = -1
    return df

def generate_discharge_data(df_adm_notes, train_id_label, val_id_label, test_id_label, df,
                            root='./data/', seed=1):
    # An example to get the train/test/split with random state:
    # note that we divide on patient admission level and share among experiments, instead of notes level.
    # This way, since our methods run on the same set of admissions, we can see the
    # progression of readmission scores.
    print('Generating discharge data ...')

    # If Discharge Summary
    df_discharge = df_adm_notes[df_adm_notes['CATEGORY'] == 'Discharge summary']
    # multiple discharge summary for one admission -> after examination
    # -> replicated summary -> replace with the last one
    df_discharge = (df_discharge.groupby(['SUBJECT_ID', 'HADM_ID']).nth(-1)).reset_index()
    df_discharge = df_discharge[df_discharge['TEXT'].notnull()]
    df_discharge = preprocessing(df_discharge)

    # get discharge train/val/test - Priya added
    discharge_train = df_discharge[df_discharge.ID.isin(train_id_label.id)]
    discharge_val = df_discharge[df_discharge.ID.isin(val_id_label.id)]
    discharge_test = df_discharge[df_discharge.ID.isin(test_id_label.id)]

    #discharge_train = unlabel(discharge_train)
    #discharge_val = unlabel(discharge_val)
    #discharge_test = unlabel(discharge_test)

    # for this set of split with random_state=1, we find we need 400 more negative training samples
    not_readmit_ID_more = df.sample(n=400, random_state=seed)
    discharge_train_snippets = pd.concat([df_discharge[df_discharge.ID.isin(not_readmit_ID_more)], discharge_train])

    # shuffle
    discharge_train_snippets = discharge_train_snippets.sample(frac=1, random_state=1).reset_index(drop=True)
    if not os.path.exists(root + 'discharge'):
        os.mkdir(root + 'discharge')
    discharge_train_snippets.to_csv(root + 'discharge/train.csv')
    discharge_val.to_csv(root + 'discharge/val.csv')
    discharge_test.to_csv(root + 'discharge/test.csv')
    print('Discharge data generated!')

def main(root='./data/', seed=1):
    adm_df = get_admissions(root)
    adm_notes_df = get_merged_adm_notes(adm_df, root)
    train_id_label, val_id_label, test_id_label, df = sample_id(adm_df, seed)
    generate_discharge_data(adm_notes_df, train_id_label, val_id_label, test_id_label, df, root, seed)
    generate_3day_data(adm_df, adm_notes_df, train_id_label, val_id_label, test_id_label, df, root, seed)
    generate_2day_data(adm_df, adm_notes_df, test_id_label, root)
    print('Complete!')


if __name__ == '__main__':
    import argparse

    parse = argparse.ArgumentParser('preprocessing script')
    parse.add_argument('--data-root', type=str, default='./data/')
    parse.add_argument('--seed', type=int, default=1)
    args = parse.parse_args()
    main(args.data_root, args.seed)
