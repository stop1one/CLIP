import torch
from torch import nn
import torch.nn.functional as F
import matplotlib.pyplot as plt
import cv2
import math
from transformers import DistilBertTokenizer
from tqdm import tqdm

import config as Config
from model import CLIPModel
from preprocess import preprocess_dataset
from dataset import FashionIQDataset, get_transforms

def get_image_embeddings(model_path):
    tokenizer = DistilBertTokenizer.from_pretrained(Config.text_tokenizer)
    dataframe = preprocess_dataset("test")
    transforms = get_transforms(mode="test")

    dataset = FashionIQDataset(
        list(zip(dataframe["target"], dataframe["candidate"])),
        dataframe["caption"].values,
        tokenizer=tokenizer,
        transforms=transforms,
    )

    valid_loader = torch.utils.data.DataLoader(
        dataset,
        batch_size=Config.batch_size,
        num_workers=Config.num_workers,
        shuffle=False,
        drop_last = True
    )
    
    model = CLIPModel().to(Config.device)
    model.load_state_dict(torch.load(model_path, map_location=Config.device))
    model.eval()
    
    valid_image_embeddings = []
    with torch.no_grad():
        for batch in tqdm(valid_loader):
            # target images = candidate_images
            target_image_features = model.image_encoder(batch["target_image"].to(Config.device))
            target_image_embeddings = model.image_projection(target_image_features)
            valid_image_embeddings.append(target_image_embeddings)

    return model, torch.cat(valid_image_embeddings), len(valid_image_embeddings)


def get_candidate_embedding(model, candidate_image):
    # cadidate image size = 3 * 224 * 224
    transforms = get_transforms("val")
    candidate_image = transforms(image=candidate_image)['image']
    candidate_image = torch.tensor(candidate_image).permute(2, 0, 1).float()
    batch = torch.empty(size=(Config.batch_size, 3, Config.size, Config.size))
    for i in range(Config.batch_size):
        batch[i] = candidate_image          # extend batch_size * 3 * 224 * 224
    candidate_features = model.image_encoder(batch.to(Config.device))
    candidate_embedding = model.image_projection(candidate_features)

    return candidate_embedding

# target_embedding: torch.Size([31728, 256]) 992 / candidate_embedding: torch.Size([31744, 256])

def find_matches(model, target_embeddings, length, candidate_image, caption, image_filenames, n=9):
    # caption process
    tokenizer = DistilBertTokenizer.from_pretrained(Config.text_tokenizer)
    encoded_caption = tokenizer([caption])
    batch = {
        key: torch.tensor(values).to(Config.device)
        for _ in range(2) for key, values in encoded_caption.items()
    }
    with torch.no_grad():
        text_features = model.text_encoder(
            input_ids=batch["input_ids"], attention_mask=batch["attention_mask"]
        )
        text_embeddings = model.text_projection(text_features)

    # candidate process
    candidate_embedding = get_candidate_embedding(model, candidate_image)
    candidate_embeddings = []
    for i in range(length): candidate_embeddings.append(candidate_embedding)    # extend
    candidate_embeddings = torch.cat(candidate_embeddings)

    # target - candidate
    image_embeddings = target_embeddings - candidate_embeddings

    image_embeddings_n = F.normalize(image_embeddings, p=2, dim=-1)
    text_embeddings_n = F.normalize(text_embeddings, p=2, dim=-1)
    dot_similarity = text_embeddings_n @ image_embeddings_n.T
    
    # multiplying by 5 to consider that there are 5 captions for a single image
    # so in indices, the first 5 indices point to a single image, the second 5 indices
    # to another one and so on.
    values, indices = torch.topk(dot_similarity.squeeze(0), n * 1)
    matches = [image_filenames[idx] for idx in indices]
    
    _, axes = plt.subplots(int(math.sqrt(n)), int(math.sqrt(n)), figsize=(10, 10))
    for match, ax in zip(matches, axes.flatten()):
        image = cv2.imread(f"{Config.image_path}/{match}.png")
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        ax.imshow(image)
        ax.axis("off")
    
    plt.show()


def find(test_caption, test_image_filename):
    test_image = cv2.imread(f"{Config.image_path}/{test_image_filename}.png")
    test_image = cv2.cvtColor(test_image, cv2.COLOR_BGR2RGB)
    cv2.imshow("test-image", test_image)
    cv2.waitKey(0)
    cv2.destroyAllWindows()

    test_df = preprocess_dataset("test")
    model, target_embeddings, length = get_image_embeddings("best.pt")
    find_matches(model,
                target_embeddings,
                length,
                caption=test_caption,
                candidate_image=test_image,
                image_filenames=test_df['target'].values,
                n=9)
