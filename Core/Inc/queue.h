/*
 * queue.h
 *
 *  Created on: Jun 22, 2024
 *      Author: 14296
 */

#ifndef INC_QUEUE_H_
#define INC_QUEUE_H_
#define transm_data_len 29

// 定义队列元素的结构体
typedef struct {
    uint8_t data[transm_data_len];
} QueueElement;

// 定义队列结构体
typedef struct {
    QueueElement *elements; // 指向队列元素数组的指针
    int front;  // 队头索引
    int rear;   // 队尾索引
    int size;   // 队列当前大小
    int capacity; // 队列容量
    uint32_t overrun_count;  // 入队满时溢出计数器
    uint32_t underrun_count; // 出队空时欠载计数器
} Queue;


Queue* createQueue(int capacity);
int isEmpty(Queue *queue);
int isFull(Queue *queue);
void enqueue(Queue *queue, uint8_t data[transm_data_len]);
QueueElement dequeue(Queue *queue);
void destroyQueue(Queue *queue);


#endif /* INC_QUEUE_H_ */
