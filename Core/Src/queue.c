/*
 * queue.c
 *
 *  Created on: Jun 22, 2024
 *      Author: 14296
 */


#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>

#include "queue.h"

// 初始化队列
Queue* createQueue(int capacity) {
    Queue *queue = (Queue*) malloc(sizeof(Queue));
    queue->capacity = capacity;
    queue->front = 0;
    queue->rear = -1;
    queue->size = 0;
    queue->elements = (QueueElement*) malloc(capacity * sizeof(QueueElement));
    return queue;
}

// 检查队列是否为空
int isEmpty(Queue *queue) {
    return queue->size == 0;
}

// 检查队列是否已满
int isFull(Queue *queue) {
    return queue->size == queue->capacity;
}

// 入队操作
void enqueue(Queue *queue, uint8_t data[transm_data_len]) {
    if (isFull(queue)) {
//        printf("Queue is full, cannot enqueue.\n");
        return;
    }
    queue->rear = (queue->rear + 1) % queue->capacity;
//    for (int i = 0; i < transm_data_len; ++i) {
//        queue->elements[queue->rear].data[i] = data[i];
//    }
    memcpy(queue->elements[queue->rear].data, data, transm_data_len);
    queue->size++;
}

// 出队操作
QueueElement dequeue(Queue *queue) {
    QueueElement item;
    if (isEmpty(queue)) {
//        printf("Queue is empty, cannot dequeue.\n");
        return item; // 返回空元素
    }
    item = queue->elements[queue->front];
    queue->front = (queue->front + 1) % queue->capacity;
    queue->size--;
    return item;
}

// 销毁队列
void destroyQueue(Queue *queue) {
    free(queue->elements);
    free(queue);
}

